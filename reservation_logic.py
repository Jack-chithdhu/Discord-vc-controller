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


def select_temp_vc_for_window(pool_entries: list, busy_windows: dict, start_time, end_time,
                              now=None, buffer_minutes: float = 15):
    """
    Time-aware pool pick: the first pool channel with no approved/active
    reservation overlapping [start, end + buffer). pool_entries: list of
    {channel_id, pool_label, status}; busy_windows: channel_id -> list of
    (start, end) for that channel's approved/active reservations. The buffer
    keeps back-to-back private reservations on different channels when possible,
    giving a previous group time to clear out.

    A channel currently 'in_use' is skipped if the new window starts right away
    (start <= now + buffer) — whoever is in it hasn't cleared out yet. Returns
    the entry, or None if every channel is taken for that window.
    """
    buffer = timedelta(minutes=buffer_minutes)
    for entry in pool_entries:
        if entry.get("status") == "in_use" and now is not None and start_time <= now + buffer:
            continue
        clash = any(
            windows_overlap(start_time, end_time + buffer, busy_start, busy_end + buffer)
            for busy_start, busy_end in busy_windows.get(entry["channel_id"], [])
        )
        if not clash:
            return entry
    return None


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
