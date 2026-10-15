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


# ---------- state machine, time windows, conflicts, expiry, pool recycling ----------

def _t(h, m=0):
    return datetime(2026, 10, 12, h, m, tzinfo=timezone.utc)


def test_approved_can_go_to_active_cancelled_or_expired_only():
    assert rl.can_transition("approved", "active")
    assert rl.can_transition("approved", "cancelled")
    assert rl.can_transition("approved", "expired")      # approved but never used, end time passed
    assert not rl.can_transition("approved", "completed")
    assert not rl.can_transition("approved", "approved")  # double-click safety
    assert not rl.can_transition("declined", "approved")


def test_window_boundaries_are_half_open():
    assert not rl.is_window_open(_t(10), _t(12), _t(9, 59))
    assert rl.is_window_open(_t(10), _t(12), _t(10))
    assert rl.is_window_open(_t(10), _t(12), _t(11, 59))
    assert not rl.is_window_open(_t(10), _t(12), _t(12))


def test_overlap_back_to_back_is_not_overlap():
    assert not rl.windows_overlap(_t(10), _t(11), _t(11), _t(12))
    assert rl.windows_overlap(_t(10), _t(11, 1), _t(11), _t(12))
    assert rl.windows_overlap(_t(10), _t(13), _t(11), _t(12))


def test_find_conflicting_reservation():
    others = [{"id": 1, "start_time": _t(10), "end_time": _t(11)}, {"id": 2, "start_time": _t(12), "end_time": _t(13)}]
    assert rl.find_conflicting_reservation(_t(10, 30), _t(12, 30), others)["id"] == 1
    assert rl.find_conflicting_reservation(_t(10, 30), _t(12, 30), others, ignore_id=1)["id"] == 2
    assert rl.find_conflicting_reservation(_t(11), _t(12), others) is None


def test_end_of_use_status():
    assert rl.end_of_use_status("active") == "completed"
    assert rl.end_of_use_status("approved") == "expired"
    assert rl.end_of_use_status("pending") is None
    assert rl.end_of_use_status("cancelled") is None


def test_should_provision_private_vc_lead():
    assert not rl.should_provision_private_vc(_t(10), _t(11), _t(9, 58))
    assert rl.should_provision_private_vc(_t(10), _t(11), _t(9, 59))
    assert rl.should_provision_private_vc(_t(10), _t(11), _t(10, 30))
    assert not rl.should_provision_private_vc(_t(10), _t(11), _t(11))


def test_local_day_start_utc_ist_and_edges():
    # 01:00 IST on 15 Oct == 19:30 UTC on 14 Oct; local midnight is 18:30 UTC on 14 Oct
    now = datetime(2026, 10, 14, 19, 30, tzinfo=timezone.utc)
    assert rl.local_day_start_utc(now, 5.5) == datetime(2026, 10, 14, 18, 30, tzinfo=timezone.utc)
    assert rl.local_day_start_utc(now, 0) == datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
    assert rl.local_day_start_utc(now, -5) == datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)


def test_pending_expires_once_end_passed_even_if_auto_expire_off():
    assert rl.pending_should_expire(_t(8), _t(10), _t(10), 24, auto_expire_enabled=False) is True
    assert rl.pending_should_expire(_t(8), _t(12), _t(10), 24, auto_expire_enabled=False) is False


def test_pending_expires_by_age_only_when_enabled():
    created = _t(8)
    now = created + timedelta(hours=25)
    end = now + timedelta(hours=2)
    assert rl.pending_should_expire(created, end, now, 24, True) is True
    assert rl.pending_should_expire(created, end, now, 24, False) is False


def test_pool_row_is_stale():
    assert rl.pool_row_is_stale("in_use", None) is True
    assert rl.pool_row_is_stale("in_use", "completed") is True
    assert rl.pool_row_is_stale("in_use", "cancelled") is True
    assert rl.pool_row_is_stale("in_use", "approved") is False
    assert rl.pool_row_is_stale("in_use", "active") is False
    assert rl.pool_row_is_stale("available", None) is False


def test_pool_recycle_ready_empty_or_after_grace():
    assert rl.pool_recycle_ready(0, _t(10), _t(10)) is True
    assert rl.pool_recycle_ready(3, None, _t(10)) is True
    assert rl.pool_recycle_ready(3, _t(10), _t(10, 13)) is False
    assert rl.pool_recycle_ready(3, _t(10), _t(10, 14)) is True


# ---------- pool allocator: windows, reuse, buffer preference ----------

def _pool(*ids):
    return [{"channel_id": str(i), "pool_label": f"Temp VC {i}", "status": "available"} for i in ids]


def test_allocator_empty_pool_returns_none():
    assert rl.select_temp_vc_for_window([], {}, _t(10), _t(11)) is None


def test_allocator_skips_overlapping_channel():
    busy = {"1": [(_t(10), _t(12))]}
    assert rl.select_temp_vc_for_window(_pool(1, 2), busy, _t(11), _t(13))["channel_id"] == "2"


def test_allocator_never_double_books_the_only_channel():
    busy = {"1": [(_t(10), _t(12))]}
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(11), _t(13)) is None
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(9), _t(10, 30)) is None   # overlaps the start
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(10, 30), _t(11)) is None  # fully inside


def test_allocator_reuses_channel_after_previous_reservation_ended():
    busy = {"1": [(_t(10), _t(12))]}
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(12), _t(13))["channel_id"] == "1"       # back to back
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(12, 5), _t(12, 8))["channel_id"] == "1"  # short slot right after


def test_allocator_buffer_is_a_preference_not_a_blocker():
    # the live bug: a 3-minute slot just after a short booking must not be refused
    busy = {"1": [(_t(12, 40), _t(12, 50))]}
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(12, 52), _t(12, 55))["channel_id"] == "1"
    # but with two channels the one with a clean gap is preferred
    assert rl.select_temp_vc_for_window(_pool(1, 2), busy, _t(12, 52), _t(12, 55))["channel_id"] == "2"


def test_allocator_ignores_stale_in_use_flag():
    pool = _pool(1)
    pool[0]["status"] = "in_use"
    assert rl.select_temp_vc_for_window(pool, {}, _t(12, 52), _t(12, 55), now=_t(12, 50))["channel_id"] == "1"


def test_pool_channel_report_explains_clash():
    busy = {"1": [(_t(10), _t(12))]}
    report = rl.pool_channel_report(_pool(1, 2), busy, _t(11), _t(13))
    assert report[0]["free"] is False and report[0]["clash"] == (_t(10), _t(12))
    assert report[1]["free"] is True and report[1]["clash"] is None


def test_suggest_next_free_start():
    busy = {"1": [(_t(10), _t(12))]}
    assert rl.suggest_next_free_start(_pool(1), busy, _t(11), _t(12), now=_t(9)) == _t(12)
    assert rl.suggest_next_free_start(_pool(1), {}, _t(11), _t(12), now=_t(9)) == _t(11)
    assert rl.suggest_next_free_start([], {}, _t(11), _t(12), now=_t(9)) is None
    # start already past -> suggestion never earlier than now
    assert rl.suggest_next_free_start(_pool(1), {}, _t(8), _t(9), now=_t(10)) == _t(10)


def test_suggest_chains_through_back_to_back_windows():
    busy = {"1": [(_t(10), _t(11)), (_t(11), _t(12))]}
    assert rl.suggest_next_free_start(_pool(1), busy, _t(10), _t(11), now=_t(9)) == _t(12)


# ---------- clock parsing and IST window building ----------

@pytest.mark.parametrize("text,expected", [
    ("7:30 PM", 19 * 60 + 30), ("7pm", 19 * 60), ("12:52 pm", 12 * 60 + 52), ("12 AM", 0),
    ("19:30", 19 * 60 + 30), ("0730", 7 * 60 + 30), ("  9:05 a.m. ", 9 * 60 + 5), ("12 PM", 12 * 60),
])
def test_parse_clock_time_ok(text, expected):
    assert rl.parse_clock_time(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "25:00", "7:75 PM", "13 PM", "0 PM", "7:3"])
def test_parse_clock_time_rejects(text):
    with pytest.raises(ValueError):
        rl.parse_clock_time(text)


def test_format_clock():
    assert rl.format_clock(12 * 60 + 52) == "12:52 PM"
    assert rl.format_clock(0) == "12:00 AM"
    assert rl.format_clock(9 * 60) == "9:00 AM"


def test_build_window_ist_matches_the_reported_booking():
    # 14 Oct 2026, 12:52-12:55 PM IST == 07:22-07:25 UTC
    from datetime import date
    now = datetime(2026, 10, 14, 7, 10, tzinfo=timezone.utc)
    start, end = rl.build_booking_window(date(2026, 10, 14), 12 * 60 + 52, 12 * 60 + 55, 5.5, now)
    assert start == datetime(2026, 10, 14, 7, 22, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 14, 7, 25, tzinfo=timezone.utc)


def test_build_window_crosses_utc_midnight_boundary():
    from datetime import date
    now = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
    start, end = rl.build_booking_window(date(2026, 10, 15), 1 * 60, 2 * 60, 5.5, now)  # 1-2 AM IST on the 15th
    assert start == datetime(2026, 10, 14, 19, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 14, 20, 30, tzinfo=timezone.utc)


def test_build_window_validation_messages():
    from datetime import date
    now = datetime(2026, 10, 14, 7, 10, tzinfo=timezone.utc)
    d = date(2026, 10, 14)
    with pytest.raises(ValueError, match="Pick a date"):
        rl.build_booking_window(None, 600, 700, 5.5, now)
    with pytest.raises(ValueError, match="start time"):
        rl.build_booking_window(d, None, 700, 5.5, now)
    with pytest.raises(ValueError, match="end time"):
        rl.build_booking_window(d, 600, None, 5.5, now)
    with pytest.raises(ValueError, match="after the start"):
        rl.build_booking_window(d, 900, 900, 5.5, now)
    with pytest.raises(ValueError, match="already passed"):
        rl.build_booking_window(d, 10 * 60, 11 * 60, 5.5, now)      # 10:00 IST = 04:30 UTC, in the past
    with pytest.raises(ValueError, match="at most"):
        rl.build_booking_window(d, 13 * 60, 13 * 60 + 13 * 60, 5.5, now)


# ---------- flexible (quarter-hour) time selection ----------
from datetime import date as _date


def test_quarter_slots_cover_each_period_with_24_options():
    assert all(len(rl.quarter_slots(i)) == 24 for i in range(4))
    assert rl.quarter_slots(3)[:5] == [1080, 1095, 1110, 1125, 1140]      # 6:00, 6:15, 6:30, 6:45, 7:00 PM
    assert rl.quarter_slots(3)[-1] == 24 * 60 - 15                         # 11:45 PM
    flat = [m for i in range(4) for m in rl.quarter_slots(i)]
    assert flat == list(range(0, 1440, 15))                                # whole day, no gaps or overlaps


def test_period_for_minute_boundaries():
    assert rl.period_for_minute(0) == 0 and rl.period_for_minute(359) == 0
    assert rl.period_for_minute(360) == 1 and rl.period_for_minute(719) == 1
    assert rl.period_for_minute(720) == 2 and rl.period_for_minute(1079) == 2
    assert rl.period_for_minute(1080) == 3 and rl.period_for_minute(1439) == 3


def test_period_labels_are_readable():
    assert rl.period_label(1) == "Morning · 6:00 AM – 12:00 PM"
    assert rl.period_label(3) == "Evening · 6:00 PM – 12:00 AM"


def test_end_options_quarter_hour_steps():
    opts = rl.end_options(19 * 60 + 15)                                    # start 7:15 PM
    assert opts[:3] == [19 * 60 + 30, 19 * 60 + 45, 20 * 60]
    assert opts[-1] == 24 * 60                                             # capped at midnight, before 6h later
    assert rl.end_options(9 * 60)[-1] == 15 * 60                           # 6h cap
    assert len(rl.end_options(9 * 60)) == 24


def test_end_options_for_exact_start_snap_to_the_grid():
    assert rl.end_options(12 * 60 + 52)[:2] == [13 * 60, 13 * 60 + 15]
    assert rl.end_options(12 * 60 + 15)[0] == 12 * 60 + 30                 # always strictly after start
    assert rl.end_options(23 * 60 + 45) == [24 * 60]
    assert rl.end_options(23 * 60 + 50) == [24 * 60]


def test_midnight_end_converts_to_next_day_utc():
    now = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    start, end = rl.build_booking_window(_date(2026, 10, 14), 23 * 60 + 15, 24 * 60, 5.5, now)
    assert start == datetime(2026, 10, 14, 17, 45, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 14, 18, 30, tzinfo=timezone.utc)      # local midnight = 18:30 UTC
    assert rl.format_clock(24 * 60) == "12:00 AM"


def test_quarter_hour_ist_conversion():
    now = datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc)
    start, end = rl.build_booking_window(_date(2026, 10, 14), 19 * 60 + 15, 19 * 60 + 45, 5.5, now)
    assert (start.hour, start.minute) == (13, 45)                          # 7:15 PM IST
    assert (end.hour, end.minute) == (14, 15)                              # 7:45 PM IST


def test_slot_is_future_boundaries():
    now = datetime(2026, 10, 14, 7, 22, tzinfo=timezone.utc)               # 12:52 IST
    d = _date(2026, 10, 14)
    assert not rl.slot_is_future(d, 12 * 60 + 45, 5.5, now)
    assert rl.slot_is_future(d, 13 * 60, 5.5, now)
    exactly_now = datetime(2026, 10, 14, 7, 30, tzinfo=timezone.utc)       # 13:00 IST
    assert not rl.slot_is_future(d, 13 * 60, 5.5, exactly_now)             # "in the future" is strict
    assert rl.slot_is_future(_date(2026, 10, 15), 0, 5.5, now)


def test_default_period_index():
    now = datetime(2026, 10, 14, 7, 22, tzinfo=timezone.utc)               # 12:52 IST -> next slot 1:00 PM
    assert rl.default_period_index(_date(2026, 10, 14), 5.5, now) == 2
    assert rl.default_period_index(_date(2026, 10, 15), 5.5, now) == 3
    late = datetime(2026, 10, 14, 18, 25, tzinfo=timezone.utc)             # 11:55 PM IST
    assert rl.default_period_index(_date(2026, 10, 14), 5.5, late) == 3


def test_format_duration_minutes():
    assert rl.format_duration_minutes(15) == "15m"
    assert rl.format_duration_minutes(60) == "1h"
    assert rl.format_duration_minutes(75) == "1h 15m"


def test_non_hourly_windows_overlap_and_boundaries():
    a = (_t(19, 15), _t(19, 45))
    assert rl.windows_overlap(*a, _t(19, 45), _t(20, 15)) is False          # touching at 7:45
    assert rl.windows_overlap(*a, _t(19, 44), _t(20, 15)) is True
    assert rl.is_window_open(*a, _t(19, 15)) and not rl.is_window_open(*a, _t(19, 45))
    busy = {"1": [a]}
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(19, 45), _t(20, 0))["channel_id"] == "1"
    assert rl.select_temp_vc_for_window(_pool(1), busy, _t(19, 30), _t(20, 0)) is None


# ---------- reminders & extensions ----------

def test_reminder_time_normal_and_short_bookings():
    assert rl.reminder_time(_t(10), _t(12)) == _t(11, 50)
    assert rl.reminder_time(_t(10), _t(10, 20)) == _t(10, 10)       # exactly 2x lead
    assert rl.reminder_time(_t(10), _t(10, 6)) == _t(10, 3)         # short: midpoint
    assert rl.reminder_time(_t(10), _t(10, 3)) == _t(10, 1) + timedelta(seconds=30)


def test_reminder_due_window_and_never_after_end():
    s, e = _t(10), _t(12)
    assert not rl.reminder_due(s, e, _t(11, 49))
    assert rl.reminder_due(s, e, _t(11, 50))
    assert rl.reminder_due(s, e, _t(11, 59))
    assert not rl.reminder_due(s, e, _t(12))                        # at/after the end: never
    assert not rl.reminder_due(s, e, _t(12, 30))


def test_reminder_not_repeated_for_same_end_but_repeats_after_extension():
    s, e = _t(10), _t(12)
    assert not rl.reminder_due(s, e, _t(11, 55), reminded_for_end=e)     # restart: already sent
    new_end = e + timedelta(minutes=30)
    assert not rl.reminder_due(s, new_end, _t(11, 55), reminded_for_end=e)  # new end not due yet
    assert rl.reminder_due(s, new_end, _t(12, 25), reminded_for_end=e)      # ...but gets its own


def test_extension_eligibility():
    now = _t(12, 5)
    assert rl.extension_eligibility("active", _t(12), now) == (True, "", False)
    assert rl.extension_eligibility("approved", _t(12, 30), now)[0] is True
    assert rl.extension_eligibility("active", _t(12), _t(12, 13))[0] is True      # tick hasn't closed it yet
    ok, _, reopen = rl.extension_eligibility("completed", _t(12), _t(12, 13, ))
    assert ok and reopen
    assert rl.extension_eligibility("completed", _t(12), _t(12, 14))[0] is False  # grace over
    for status in ("pending", "declined", "cancelled", "expired"):
        ok, reason, _ = rl.extension_eligibility(status, _t(12), now)
        assert not ok and reason


def test_extended_end_uses_later_of_end_and_now():
    assert rl.extended_end(_t(12), _t(11, 50), 15) == _t(12, 15)
    assert rl.extended_end(_t(12), _t(12, 5), 15) == _t(12, 20)            # asked 5 min late


def test_max_extension_and_length_cap():
    assert rl.max_extension_minutes(_t(12), _t(12, 40)) == 40
    assert rl.max_extension_minutes(_t(12), _t(12)) == 0
    assert rl.max_extension_minutes(_t(12), _t(11)) == 0
    assert not rl.extension_too_long(_t(10), _t(22), 720)
    assert rl.extension_too_long(_t(10), _t(22, 1), 720)
