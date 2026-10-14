"""
reservation_logic.py — pure, side-effect-free VC reservation logic.

Same philosophy as vc_stats.py: zero dependency on discord.py, Flask, or
psycopg2. Every function takes plain data in and returns plain data out,
so the state machine and spam-control rules can be unit tested (see
test_reservation_logic.py) without a live database or Discord connection.

app.py's job is to fetch/store rows and call these functions — it should
not reimplement transition validity or limit-checking math itself.
"""

from datetime import datetime, timezone, timedelta
import re


# ---------- Reservation state machine ----------

VALID_TRANSITIONS = {
    "pending": {"approved", "declined", "expired", "cancelled"},
    # 'expired' here = approved but nobody ever used it and its end time passed
    # (a no-show). Without this edge such a reservation could never finish, and
    # a locked one kept restricting its channel forever.
    "approved": {"active", "cancelled", "expired"},
    "active": {"completed", "cancelled"},
    "declined": set(),
    "expired": set(),
    "cancelled": set(),
    "completed": set(),
}

ALL_STATUSES = set(VALID_TRANSITIONS.keys())


def can_transition(current_status: str, new_status: str) -> bool:
    """The database row is the only source of truth for reservation state
    (never a Discord message) — this is what every status write must check
    before applying, so an approval click on an already-declined request,
    a double-click, or a stale button can never corrupt the state machine."""
    return new_status in VALID_TRANSITIONS.get(current_status, set())


def is_terminal(status: str) -> bool:
    return len(VALID_TRANSITIONS.get(status, set())) == 0


# ---------- Spam control ----------

DEFAULT_SPAM_CONFIG = {
    "spam_protection_enabled": True,
    "cooldown_minutes": 30,
    "max_pending": 1,
    "max_per_day": 3,
    "max_active": 1,
    "duplicate_protection_enabled": True,
}


def evaluate_new_reservation_request(
    *,
    pending_count: int,
    active_count: int,
    reservations_today_count: int,
    last_request_time,  # datetime or None
    now,
    requested_start,
    requested_end,
    existing_time_ranges: list,  # list of (start, end) tuples for this user's current pending/approved/active reservations
    config: dict = None,
) -> tuple:
    """
    Returns (allowed: bool, reason: str). reason is "" when allowed.

    Every check is independent and short-circuits on the first violation,
    in the order: cooldown -> pending cap -> active cap -> daily cap ->
    duplicate/overlap. Disabling spam_protection_enabled bypasses
    everything; disabling duplicate_protection_enabled only skips the
    overlap check, leaving the numeric caps in effect.
    """
    cfg = {**DEFAULT_SPAM_CONFIG, **(config or {})}

    if not cfg["spam_protection_enabled"]:
        return True, ""

    if cfg["cooldown_minutes"] and last_request_time is not None:
        elapsed_minutes = (now - last_request_time).total_seconds() / 60
        if elapsed_minutes < cfg["cooldown_minutes"]:
            remaining = cfg["cooldown_minutes"] - elapsed_minutes
            return False, f"Please wait {remaining:.0f} more minute(s) before requesting again."

    if pending_count >= cfg["max_pending"]:
        return False, "You already have a pending reservation. Please wait for it to be approved or declined."

    if active_count >= cfg["max_active"]:
        return False, "You already have an active reservation."

    if reservations_today_count >= cfg["max_per_day"]:
        return False, "You have reached today's reservation limit."

    if cfg["duplicate_protection_enabled"]:
        for existing_start, existing_end in existing_time_ranges:
            if requested_start < existing_end and existing_start < requested_end:
                return False, "This overlaps one of your existing reservations."

    return True, ""


def is_pending_expired(created_at, now, expire_after_hours: float) -> bool:
    return (now - created_at).total_seconds() / 3600 >= expire_after_hours


# ---------- Temporary VC pool ----------

def select_available_temp_vc(pool_entries: list):
    """pool_entries: list of {channel_id, pool_label, status}. Returns the
    first available entry, or None if the whole pool is currently in use —
    the caller is expected to tell the requester to wait or notify admins,
    never to create a brand-new Discord channel as a fallback."""
    for entry in pool_entries:
        if entry["status"] == "available":
            return entry
    return None


def generate_temp_vc_name(requester_name: str, reservation_name: str) -> str:
    """Deterministic display name for a temp VC while it's in use — the
    exact emoji/decoration is a Discord-side UI concern (Phase 3), this just
    guarantees a consistent, readable base name."""
    label = reservation_name.strip() if reservation_name and reservation_name.strip() else "VC"
    return f"{requester_name}'s {label}"


# ---------- Booking form parsing (pure — no Discord/DB) ----------

_MENTION_OR_ID_RE = re.compile(r"<@!?(\d{15,20})>|(?<!\d)(\d{15,20})(?!\d)")


def parse_member_ids(text: str) -> list:
    """Extracts Discord user IDs from free text — handles both pasted
    @mentions (<@123...>) and bare IDs, comma/space/newline separated.
    Returns them in first-seen order with duplicates removed."""
    if not text:
        return []
    seen = []
    for match in _MENTION_OR_ID_RE.finditer(text):
        uid = match.group(1) or match.group(2)
        if uid and uid not in seen:
            seen.append(uid)
    return seen


def parse_reservation_datetime(text: str) -> datetime:
    """Parses the booking modals' combined 'YYYY-MM-DD HH:MM' field into a
    naive datetime (caller applies timezone offset). Raises ValueError on
    anything that doesn't match, so the modal can show one clear error."""
    return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")


def parse_reservation_end_time(text: str) -> tuple:
    """Parses the end-time field, which is just 'HH:MM' (same day as start
    — Phase 2's booking modals don't support overnight reservations).
    Returns (hour, minute)."""
    parsed = datetime.strptime(text.strip(), "%H:%M")
    return parsed.hour, parsed.minute


# ---------- Native-component form option generators (pure) ----------
# Used by the Existing VC booking form's dropdowns — everything here is
# deterministic date/string math with zero Discord dependency, so it's
# fully unit-testable on its own.

def generate_date_options(today, days_ahead: int = 14) -> list:
    """Returns up to `days_ahead` (date, label) pairs starting today, for a
    date-picker Select — avoids making anyone type a date by hand. `today`
    should already be the LOCAL date (caller applies the timezone offset).
    Discord Select menus cap at 25 options, so days_ahead should stay <=25."""
    options = []
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        if i == 0:
            label = f"Today \u2014 {d.strftime('%d %b %Y')}"
        elif i == 1:
            label = f"Tomorrow \u2014 {d.strftime('%d %b %Y')}"
        else:
            label = d.strftime("%a, %d %b %Y")
        options.append((d, label))
    return options


def generate_hourly_time_options() -> list:
    """Returns the 24 on-the-hour options for a day in 12-hour AM/PM format,
    e.g. ('0', '12:00 AM'), ('13', '1:00 PM') — exactly fits Discord's
    25-option-per-Select cap, so both a Start and an End time picker can
    each cover the full day without needing free-text entry."""
    options = []
    for hour in range(24):
        label = datetime(2000, 1, 1, hour, 0).strftime("%I:%M %p").lstrip("0")
        options.append((str(hour), label))
    return options


# ---------- Temp VC lifecycle decisions (pure) ----------

def should_recycle_temp_vc(member_count: int, pool_status: str) -> bool:
    """A temp VC gets hidden/renamed/returned to the pool exactly when it's
    currently serving a reservation (in_use) AND has emptied out — never
    for a channel that's just sitting available already, and never while
    someone's still in it."""
    return pool_status == "in_use" and member_count == 0


def is_reservation_overdue(end_time, now, grace_minutes: float = 15) -> bool:
    """Safety net for abandoned reservations: an approved/active reservation
    past its end_time (plus a small grace window, so a reservation ending
    right now isn't force-completed a second too early) should be force-
    completed even if nobody ever explicitly ended it."""
    return now >= end_time + timedelta(minutes=grace_minutes)


# ---------- Reservation time windows (pure) ----------
# A reservation only restricts anything between its start and end. All times
# are timezone-aware UTC datetimes (the caller converts local booking times
# using the one TIMEZONE_OFFSET_HOURS setting before they reach here).

def is_window_open(start_time, end_time, now) -> bool:
    """True while the reservation is in force: start <= now < end. Before the
    start and from the end onward, its rules (lock, private-VC access) must not
    apply — this is the single definition every enforcement check uses."""
    return start_time <= now < end_time


def windows_overlap(a_start, a_end, b_start, b_end) -> bool:
    """Half-open interval overlap: back-to-back bookings (one ends exactly when
    the next starts) do NOT overlap."""
    return a_start < b_end and b_start < a_end


def find_conflicting_reservation(start_time, end_time, others: list, ignore_id=None):
    """First reservation in `others` (dicts with id/start_time/end_time) whose
    window overlaps [start_time, end_time), skipping `ignore_id`. None if the
    slot is free."""
    for other in others:
        if ignore_id is not None and other.get("id") == ignore_id:
            continue
        if windows_overlap(start_time, end_time, other["start_time"], other["end_time"]):
            return other
    return None


def end_of_use_status(current_status: str):
    """Status a reservation moves to once its end time has passed: an active
    one (people used it) is 'completed'; an approved one nobody ever used is
    'expired'. Anything else is not a live reservation — None means 'leave it'."""
    return {"active": "completed", "approved": "expired"}.get(current_status)


def should_provision_private_vc(start_time, end_time, now, lead_minutes: float = 1) -> bool:
    """A private (pool) VC is set up for its members from a short lead before the
    start (so it's ready the moment the window opens, despite the scheduler tick
    granularity) until the end. The pool channel is hidden from everyone else the
    whole time, so the small lead never affects normal channel access."""
    return start_time - timedelta(minutes=lead_minutes) <= now < end_time


def pool_channel_report(pool_entries: list, busy_windows: dict, start_time, end_time,
                        buffer_minutes: float = 0) -> list:
    """Per-channel availability for [start, end): one dict per pool entry with
    channel_id, pool_label, free (bool) and clash ((start, end) of the first
    overlapping approved/active window, or None). The buffer widens the new
    window on both sides, i.e. requires that much quiet time around it."""
    buffer = timedelta(minutes=buffer_minutes)
    report = []
    for entry in pool_entries:
        clash = None
        for busy_start, busy_end in busy_windows.get(entry["channel_id"], []):
            if windows_overlap(start_time - buffer, end_time + buffer, busy_start, busy_end):
                clash = (busy_start, busy_end)
                break
        report.append({"channel_id": entry["channel_id"], "pool_label": entry.get("pool_label"),
                       "free": clash is None, "clash": clash})
    return report


def select_temp_vc_for_window(pool_entries: list, busy_windows: dict, start_time, end_time,
                              now=None, buffer_minutes: float = 15):
    """
    Picks a pool channel for [start, end): the first one with no approved/active
    reservation overlapping that window. pool_entries: list of {channel_id,
    pool_label, status}; busy_windows: channel_id -> [(start, end), ...].

    Two passes: first prefer a channel with `buffer_minutes` of quiet time around
    the slot (so a previous group has time to clear out), then fall back to plain
    no-overlap — a buffer is a preference, never a reason to refuse a free slot.
    A channel's in_use flag is deliberately ignored: it can be stale, and a real
    conflict always shows up as an overlapping reservation window. (`now` is kept
    for call compatibility.) Returns the entry or None.
    """
    for buf in (buffer_minutes, 0):
        for entry, row in zip(pool_entries, pool_channel_report(pool_entries, busy_windows, start_time, end_time, buf)):
            if row["free"]:
                return entry
    return None


def suggest_next_free_start(pool_entries: list, busy_windows: dict, start_time, end_time,
                            now, horizon_hours: float = 24):
    """Earliest start >= max(requested start, now) within the horizon at which
    some pool channel is free for the same duration, or None (empty pool / nothing
    free in the horizon). Candidates are the requested start and every moment a
    busy window ends."""
    if not pool_entries:
        return None
    duration = end_time - start_time
    earliest = max(start_time, now)
    candidates = {earliest}
    for windows in busy_windows.values():
        for _, busy_end in windows:
            if busy_end > earliest:
                candidates.add(busy_end)
    for candidate in sorted(candidates):
        if candidate > earliest + timedelta(hours=horizon_hours):
            break
        if any(r["free"] for r in pool_channel_report(pool_entries, busy_windows, candidate, candidate + duration)):
            return candidate
    return None


# ---------- Private VC booking form: time parsing / validation (pure) ----------

def parse_clock_time(text: str) -> int:
    """'7:30 PM', '7pm', '19:30', '0730' -> minutes since midnight. ValueError
    with a friendly message on anything else."""
    raw = (text or "").strip().lower().replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm)?", raw)
    if not m:
        raise ValueError("Use a time like 7:30 PM or 19:30.")
    hour, minute, suffix = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if minute > 59:
        raise ValueError("Minutes must be between 00 and 59.")
    if suffix:
        if not 1 <= hour <= 12:
            raise ValueError("With AM/PM the hour must be 1\u201312.")
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    elif hour > 23:
        raise ValueError("Hour must be between 0 and 23.")
    return hour * 60 + minute


def format_clock(minutes: int) -> str:
    """Minutes since midnight -> '12:52 PM'."""
    return datetime(2000, 1, 1, minutes // 60 % 24, minutes % 60).strftime("%I:%M %p").lstrip("0")


def build_booking_window(date_local, start_min, end_min, offset_hours: float, now,
                         max_minutes: int = 12 * 60):
    """Validates a same-day booking chosen as a local date + start/end minutes and
    returns (start_utc, end_utc). Raises ValueError with a user-facing message.
    `date_local` is a date in the server's local time (UTC + offset_hours)."""
    if date_local is None:
        raise ValueError("Pick a date.")
    if start_min is None:
        raise ValueError("Pick a start time.")
    if end_min is None:
        raise ValueError("Pick an end time.")
    if end_min <= start_min:
        raise ValueError("End time must be after the start time (same-day bookings only).")
    if end_min - start_min > max_minutes:
        raise ValueError(f"Bookings can be at most {max_minutes // 60} hours long.")
    midnight = datetime(date_local.year, date_local.month, date_local.day, tzinfo=timezone.utc)
    start_utc = midnight + timedelta(minutes=start_min) - timedelta(hours=offset_hours)
    end_utc = midnight + timedelta(minutes=end_min) - timedelta(hours=offset_hours)
    if start_utc < now:
        raise ValueError("That start time has already passed \u2014 pick a later time.")
    return start_utc, end_utc


# ---------- Local (India) calendar-day boundary ----------

def local_day_start_utc(now, offset_hours: float):
    """UTC instant of the most recent local midnight, where local = UTC + offset.
    The daily booking limit counts from here, so 'today' means the server's local
    day (IST) rather than the UTC day, which would reset at 5:30 AM IST."""
    local_now = now + timedelta(hours=offset_hours)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - timedelta(hours=offset_hours)


# ---------- Expiry / pool-recycling decisions (pure) ----------

def pending_should_expire(created_at, end_time, now, expire_after_hours: float,
                          auto_expire_enabled: bool = True) -> bool:
    """A still-pending request can never be granted once its end time has passed
    (always expires), and also expires after sitting unanswered too long when
    auto-expiry is on."""
    if end_time <= now:
        return True
    return bool(auto_expire_enabled) and is_pending_expired(created_at, now, expire_after_hours)


def pool_row_is_stale(pool_status: str, reservation_status) -> bool:
    """A pool channel marked in_use whose reservation is no longer approved/active
    (finished, cancelled, deleted -> None) is a leftover that must be reset."""
    return pool_status == "in_use" and reservation_status not in ("approved", "active")


def pool_recycle_ready(member_count: int, reservation_end, now, grace_minutes: float = 14) -> bool:
    """Whether a leftover pool channel can be reset now: immediately if empty (or
    its reservation is unknown), otherwise only once the grace period after the
    reservation's end has run out, so a group finishing a conversation isn't cut
    off the second the clock strikes. Must stay shorter than the pool buffer used
    by select_temp_vc_for_window so the next booking never waits on it."""
    if member_count == 0 or reservation_end is None:
        return True
    return now >= reservation_end + timedelta(minutes=grace_minutes)


# ---------- Flexible time selection (pure) ----------
# Discord has no time-picker component and a select menu holds at most 25 options,
# so the booking forms offer 15-minute steps through a "time of day" select (four
# 6-hour periods of 24 quarter-hour slots each) plus an Exact times popup that
# accepts any minute. Everything below works in minutes since local midnight.

TIME_STEP_MINUTES = 15
MAX_DROPDOWN_DURATION_MINUTES = 6 * 60   # longer bookings use Exact times

TIME_PERIODS = [
    ("Night", 0, 6 * 60),
    ("Morning", 6 * 60, 12 * 60),
    ("Afternoon", 12 * 60, 18 * 60),
    ("Evening", 18 * 60, 24 * 60),
]


def period_for_minute(minute: int) -> int:
    """Index into TIME_PERIODS containing this minute-of-day."""
    for i, (_, lo, hi) in enumerate(TIME_PERIODS):
        if lo <= minute < hi:
            return i
    return len(TIME_PERIODS) - 1


def period_label(index: int) -> str:
    name, lo, hi = TIME_PERIODS[index]
    return f"{name} · {format_clock(lo)} – {format_clock(hi % 1440)}"


def quarter_slots(period_index: int, step: int = TIME_STEP_MINUTES) -> list:
    _, lo, hi = TIME_PERIODS[period_index]
    return list(range(lo, hi, step))


def slot_is_future(date_local, minute: int, offset_hours: float, now) -> bool:
    """True if local `date_local` at `minute` is strictly after `now` (aware UTC)."""
    midnight = datetime(date_local.year, date_local.month, date_local.day, tzinfo=timezone.utc)
    return midnight + timedelta(minutes=minute) - timedelta(hours=offset_hours) > now


def default_period_index(date_local, offset_hours: float, now) -> int:
    """Period to show first: the one holding the next free quarter-hour when
    booking for today, otherwise the evening."""
    local_now = now + timedelta(hours=offset_hours)
    if date_local == local_now.date():
        minute = local_now.hour * 60 + local_now.minute
        next_slot = (minute // TIME_STEP_MINUTES + 1) * TIME_STEP_MINUTES
        return period_for_minute(min(next_slot, 1439))
    return len(TIME_PERIODS) - 1


def end_options(start_min: int, step: int = TIME_STEP_MINUTES,
                max_duration: int = MAX_DROPDOWN_DURATION_MINUTES) -> list:
    """End times offered for a start: the next quarter-hour boundary after the
    start, then every step, up to max_duration later or midnight (1440 = 12:00 AM
    at the end of the day), whichever is first. Works for any start minute, so an
    exact start like 12:52 offers 1:00 PM, 1:15 PM, ..."""
    first = (start_min // step + 1) * step
    last = min(start_min + max_duration, 24 * 60)
    return list(range(first, last + 1, step))


def format_duration_minutes(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"
