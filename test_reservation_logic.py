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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
