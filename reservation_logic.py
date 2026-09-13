"""
reservation_logic.py — pure, side-effect-free VC reservation logic.

Same philosophy as vc_stats.py: zero dependency on discord.py, Flask, or
psycopg2. Every function takes plain data in and returns plain data out,
so the state machine and spam-control rules can be unit tested (see
test_reservation_logic.py) without a live database or Discord connection.

app.py's job is to fetch/store rows and call these functions — it should
not reimplement transition validity or limit-checking math itself.
"""

from datetime import datetime, timezone
import re


# ---------- Reservation state machine ----------

VALID_TRANSITIONS = {
    "pending": {"approved", "declined", "expired", "cancelled"},
    "approved": {"active", "cancelled"},
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
