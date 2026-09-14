"""
test_reservation_logic.py — automated tests for reservation_logic.py.

Run with: pytest test_reservation_logic.py -v

No database, no Discord connection — pure function tests, same philosophy
as test_vc_stats.py.
"""

from datetime import datetime, timezone, timedelta
import pytest

import reservation_logic as rl


def dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# ---------- State machine ----------

def test_pending_can_go_to_approved_declined_expired_cancelled():
    for target in ("approved", "declined", "expired", "cancelled"):
        assert rl.can_transition("pending", target)


def test_pending_cannot_go_directly_to_active_or_completed():
    assert not rl.can_transition("pending", "active")
    assert not rl.can_transition("pending", "completed")


def test_approved_can_go_to_active_or_cancelled_only():
    assert rl.can_transition("approved", "active")
    assert rl.can_transition("approved", "cancelled")
    assert not rl.can_transition("approved", "declined")
    assert not rl.can_transition("approved", "completed")


def test_active_can_go_to_completed_or_cancelled_only():
    assert rl.can_transition("active", "completed")
    assert rl.can_transition("active", "cancelled")
    assert not rl.can_transition("active", "approved")


def test_terminal_states_have_no_outgoing_transitions():
    for terminal in ("declined", "expired", "cancelled", "completed"):
        assert rl.is_terminal(terminal)
        for target in rl.ALL_STATUSES:
            assert not rl.can_transition(terminal, target)


def test_double_approve_is_rejected():
    """Clicking Approve twice must not succeed the second time — this is
    exactly the 'idempotent actions' requirement from the spec."""
    assert rl.can_transition("pending", "approved")  # first click: valid
    # after the first click, status is now "approved" — a second Approve
    # click would attempt "approved" -> "approved", which isn't a valid
    # transition target at all (not in the transition map's outputs)
    assert not rl.can_transition("approved", "approved")


def test_decline_after_approval_is_rejected():
    assert not rl.can_transition("approved", "declined")


# ---------- Spam control ----------

def test_spam_protection_disabled_bypasses_everything():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=99, active_count=99, reservations_today_count=99,
        last_request_time=dt(2026, 1, 1, 0, 0), now=dt(2026, 1, 1, 0, 1),
        requested_start=dt(2026, 1, 1, 1, 0), requested_end=dt(2026, 1, 1, 2, 0),
        existing_time_ranges=[(dt(2026, 1, 1, 1, 0), dt(2026, 1, 1, 2, 0))],
        config={"spam_protection_enabled": False},
    )
    assert allowed
    assert reason == ""


def test_cooldown_blocks_rapid_requests():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=0,
        last_request_time=dt(2026, 1, 1, 0, 0), now=dt(2026, 1, 1, 0, 10),
        requested_start=dt(2026, 1, 2, 0, 0), requested_end=dt(2026, 1, 2, 1, 0),
        existing_time_ranges=[], config={"cooldown_minutes": 30},
    )
    assert not allowed
    assert "wait" in reason.lower()


def test_cooldown_passes_after_enough_time():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=0,
        last_request_time=dt(2026, 1, 1, 0, 0), now=dt(2026, 1, 1, 0, 31),
        requested_start=dt(2026, 1, 2, 0, 0), requested_end=dt(2026, 1, 2, 1, 0),
        existing_time_ranges=[], config={"cooldown_minutes": 30},
    )
    assert allowed


def test_max_pending_cap():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=1, active_count=0, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 0), requested_end=dt(2026, 1, 2, 1, 0),
        existing_time_ranges=[], config={"max_pending": 1},
    )
    assert not allowed
    assert "pending" in reason.lower()


def test_max_active_cap():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=1, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 0), requested_end=dt(2026, 1, 2, 1, 0),
        existing_time_ranges=[], config={"max_active": 1},
    )
    assert not allowed
    assert "active" in reason.lower()


def test_max_per_day_cap():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=3,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 0), requested_end=dt(2026, 1, 2, 1, 0),
        existing_time_ranges=[], config={"max_per_day": 3},
    )
    assert not allowed
    assert "limit" in reason.lower()


def test_duplicate_overlap_blocked():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 30), requested_end=dt(2026, 1, 2, 1, 30),
        existing_time_ranges=[(dt(2026, 1, 2, 0, 0), dt(2026, 1, 2, 1, 0))],  # overlaps 0:30-1:00
        config={"duplicate_protection_enabled": True},
    )
    assert not allowed
    assert "overlap" in reason.lower()


def test_back_to_back_not_considered_overlap():
    """A request starting exactly when another ends shouldn't be blocked as a duplicate."""
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 1, 0), requested_end=dt(2026, 1, 2, 2, 0),
        existing_time_ranges=[(dt(2026, 1, 2, 0, 0), dt(2026, 1, 2, 1, 0))],
        config={"duplicate_protection_enabled": True},
    )
    assert allowed


def test_duplicate_protection_disabled_allows_overlap_but_caps_still_apply():
    allowed, reason = rl.evaluate_new_reservation_request(
        pending_count=0, active_count=0, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 30), requested_end=dt(2026, 1, 2, 1, 30),
        existing_time_ranges=[(dt(2026, 1, 2, 0, 0), dt(2026, 1, 2, 1, 0))],
        config={"duplicate_protection_enabled": False},
    )
    assert allowed  # overlap check skipped
    # but caps are independent — confirm they'd still fire if violated
    blocked, reason2 = rl.evaluate_new_reservation_request(
        pending_count=1, active_count=0, reservations_today_count=0,
        last_request_time=None, now=dt(2026, 1, 1, 0, 0),
        requested_start=dt(2026, 1, 2, 0, 30), requested_end=dt(2026, 1, 2, 1, 30),
        existing_time_ranges=[(dt(2026, 1, 2, 0, 0), dt(2026, 1, 2, 1, 0))],
        config={"duplicate_protection_enabled": False, "max_pending": 1},
    )
    assert not blocked


# ---------- Auto-expiry of pending requests ----------

def test_pending_expiry_boundary():
    created = dt(2026, 1, 1, 0, 0)
    assert not rl.is_pending_expired(created, dt(2026, 1, 1, 23, 59), expire_after_hours=24)
    assert rl.is_pending_expired(created, dt(2026, 1, 2, 0, 0), expire_after_hours=24)
    assert rl.is_pending_expired(created, dt(2026, 1, 3, 0, 0), expire_after_hours=24)


# ---------- Temporary VC pool selection ----------

def test_select_available_temp_vc_picks_first_available():
    pool = [
        {"channel_id": "1", "pool_label": "Temp VC 01", "status": "in_use"},
        {"channel_id": "2", "pool_label": "Temp VC 02", "status": "available"},
        {"channel_id": "3", "pool_label": "Temp VC 03", "status": "available"},
    ]
    picked = rl.select_available_temp_vc(pool)
    assert picked["channel_id"] == "2"


def test_select_available_temp_vc_returns_none_when_pool_full():
    pool = [
        {"channel_id": "1", "pool_label": "Temp VC 01", "status": "in_use"},
        {"channel_id": "2", "pool_label": "Temp VC 02", "status": "in_use"},
    ]
    assert rl.select_available_temp_vc(pool) is None


def test_select_available_temp_vc_empty_pool():
    assert rl.select_available_temp_vc([]) is None


# ---------- Temp VC naming (Rahul's Gaming / Alex's Gaming reuse scenario) ----------

def test_generate_temp_vc_name_basic():
    assert rl.generate_temp_vc_name("Rahul", "Gaming") == "Rahul's Gaming"


def test_generate_temp_vc_name_reused_channel_different_reservations():
    """The exact spec scenario: the same pool channel serves two different
    reservations at different times — names must differ and neither should
    leak into the other."""
    name_1 = rl.generate_temp_vc_name("Rahul", "Gaming")
    name_2 = rl.generate_temp_vc_name("Alex", "Gaming")
    assert name_1 == "Rahul's Gaming"
    assert name_2 == "Alex's Gaming"
    assert name_1 != name_2


def test_generate_temp_vc_name_blank_reservation_name_falls_back():
    assert rl.generate_temp_vc_name("Rahul", "") == "Rahul's VC"
    assert rl.generate_temp_vc_name("Rahul", "   ") == "Rahul's VC"


# ---------- Booking form parsing ----------

def test_parse_member_ids_from_mentions():
    text = "<@123456789012345678> and <@!987654321098765432>"
    assert rl.parse_member_ids(text) == ["123456789012345678", "987654321098765432"]


def test_parse_member_ids_from_bare_ids_comma_separated():
    text = "123456789012345678, 987654321098765432"
    assert rl.parse_member_ids(text) == ["123456789012345678", "987654321098765432"]


def test_parse_member_ids_dedupes_preserving_order():
    text = "<@123456789012345678> 123456789012345678 <@123456789012345678>"
    assert rl.parse_member_ids(text) == ["123456789012345678"]


def test_parse_member_ids_ignores_short_numbers():
    """A duration like '60' or a date fragment shouldn't be mistaken for a Discord ID."""
    text = "60 minutes with <@123456789012345678>"
    assert rl.parse_member_ids(text) == ["123456789012345678"]


def test_parse_member_ids_empty_input():
    assert rl.parse_member_ids("") == []
    assert rl.parse_member_ids(None) == []


def test_parse_reservation_datetime_valid():
    result = rl.parse_reservation_datetime("2026-09-20 19:30")
    assert result == datetime(2026, 9, 20, 19, 30)


def test_parse_reservation_datetime_invalid_raises():
    with pytest.raises(ValueError):
        rl.parse_reservation_datetime("not a date")


def test_parse_reservation_end_time_valid():
    assert rl.parse_reservation_end_time("21:45") == (21, 45)


def test_parse_reservation_end_time_invalid_raises():
    with pytest.raises(ValueError):
        rl.parse_reservation_end_time("25:99")


# ---------- Temp VC lifecycle decisions ----------

def test_should_recycle_when_in_use_and_empty():
    assert rl.should_recycle_temp_vc(member_count=0, pool_status="in_use")


def test_should_not_recycle_when_in_use_but_occupied():
    assert not rl.should_recycle_temp_vc(member_count=1, pool_status="in_use")


def test_should_not_recycle_when_already_available():
    """An empty channel that's already available (not serving anyone) isn't a recycle event."""
    assert not rl.should_recycle_temp_vc(member_count=0, pool_status="available")


def test_reservation_overdue_respects_grace_period():
    end_time = dt(2026, 1, 1, 12, 0)
    assert not rl.is_reservation_overdue(end_time, dt(2026, 1, 1, 12, 10), grace_minutes=15)
    assert rl.is_reservation_overdue(end_time, dt(2026, 1, 1, 12, 15), grace_minutes=15)
    assert rl.is_reservation_overdue(end_time, dt(2026, 1, 1, 13, 0), grace_minutes=15)


def test_reservation_not_overdue_before_end_time():
    end_time = dt(2026, 1, 1, 12, 0)
    assert not rl.is_reservation_overdue(end_time, dt(2026, 1, 1, 11, 0), grace_minutes=15)


# ---------- Native-component form option generators ----------

def test_generate_date_options_labels_today_and_tomorrow_specially():
    today = dt(2026, 9, 14).date()
    options = rl.generate_date_options(today, days_ahead=5)
    assert len(options) == 5
    assert options[0][0] == today
    assert options[0][1].startswith("Today")
    assert options[1][1].startswith("Tomorrow")
    assert "Today" not in options[2][1]
    assert "Tomorrow" not in options[2][1]


def test_generate_date_options_count_matches_days_ahead():
    today = dt(2026, 1, 1).date()
    assert len(rl.generate_date_options(today, days_ahead=14)) == 14
    assert len(rl.generate_date_options(today, days_ahead=25)) == 25  # stays within Discord's Select cap


def test_generate_date_options_dates_are_sequential():
    today = dt(2026, 1, 1).date()
    options = rl.generate_date_options(today, days_ahead=5)
    dates = [d for d, _ in options]
    assert dates == [today + timedelta(days=i) for i in range(5)]


def test_generate_hourly_time_options_has_24_entries_within_select_cap():
    options = rl.generate_hourly_time_options()
    assert len(options) == 24
    assert len(options) <= 25  # Discord's per-Select option cap


def test_generate_hourly_time_options_values_are_24h_hour_strings():
    options = rl.generate_hourly_time_options()
    values = [v for v, _ in options]
    assert values == [str(h) for h in range(24)]


def test_generate_hourly_time_options_labels_are_12h_am_pm():
    options = dict(rl.generate_hourly_time_options())
    assert options["0"] == "12:00 AM"
    assert options["12"] == "12:00 PM"
    assert options["13"] == "1:00 PM"
    assert options["23"] == "11:00 PM"
    # no leading zero on single-digit hours, matching the reference UI (e.g. "7:30 PM" not "07:30 PM")
    assert options["7"] == "7:00 AM"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
