"""
test_vc_stats.py — automated tests for the vc_stats.py pure logic module.

Run with: pytest test_vc_stats.py -v

No database, no Discord connection, no network — every test constructs
plain event/session dicts and asserts on the math directly. This is
possible specifically because vc_stats.py has zero I/O dependencies.
"""

from datetime import datetime, timezone, timedelta
import pytest

import vc_stats as vs


def dt(y, m, d, h=0, mi=0, s=0):
    return datetime(y, m, d, h, mi, s, tzinfo=timezone.utc)


def ev(action, uid, uname, t, from_ch=None, to_ch=None):
    return {
        "action": action, "user_id": uid, "user_name": uname,
        "from_channel_name": from_ch, "to_channel_name": to_ch,
        "created_at": t,
    }


# ---------- 1. Basic session reconstruction ----------

def test_join_leave_simple():
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 0), to_ch="General"),
        ev("LEFT", "u1", "Alice", dt(2026, 1, 1, 11, 0), from_ch="General"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 1
    assert sessions[0]["duration_seconds"] == 3600
    assert sessions[0]["channel_name"] == "General"


def test_join_disconnect():
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 0), to_ch="General"),
        ev("DISCONNECTED", "u1", "Alice", dt(2026, 1, 1, 10, 30), from_ch="General"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 1
    assert sessions[0]["duration_seconds"] == 1800


# ---------- 2. MOVE handling, including multi-hop ----------

def test_join_move_leave():
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 0), to_ch="A"),
        ev("MOVED", "u1", "Alice", dt(2026, 1, 1, 10, 30), from_ch="A", to_ch="B"),
        ev("LEFT", "u1", "Alice", dt(2026, 1, 1, 11, 0), from_ch="B"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 2
    a_session = next(s for s in sessions if s["channel_name"] == "A")
    b_session = next(s for s in sessions if s["channel_name"] == "B")
    assert a_session["duration_seconds"] == 1800  # 10:00-10:30
    assert b_session["duration_seconds"] == 1800  # 10:30-11:00
    total = sum(s["duration_seconds"] for s in sessions)
    assert total == 3600  # exactly matches actual time in VC, nothing lost/duplicated


def test_multiple_moves_a_b_c_a_b_leave():
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 0), to_ch="A"),
        ev("MOVED", "u1", "Alice", dt(2026, 1, 1, 10, 10), from_ch="A", to_ch="B"),
        ev("MOVED", "u1", "Alice", dt(2026, 1, 1, 10, 20), from_ch="B", to_ch="C"),
        ev("MOVED", "u1", "Alice", dt(2026, 1, 1, 10, 30), from_ch="C", to_ch="A"),
        ev("MOVED", "u1", "Alice", dt(2026, 1, 1, 10, 40), from_ch="A", to_ch="B"),
        ev("LEFT", "u1", "Alice", dt(2026, 1, 1, 10, 50), from_ch="B"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 5
    total = sum(s["duration_seconds"] for s in sessions)
    assert total == 50 * 60  # 10:00 -> 10:50, no gaps/overlaps


# ---------- 3. Duplicate / missing events ----------

def test_duplicate_join_defensively_closes_first():
    """Two JOINED events with no LEFT between them shouldn't lose or duplicate time —
    the first is defensively closed the moment the second arrives."""
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 0), to_ch="A"),
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 10, 5), to_ch="A"),  # duplicate/glitch
        ev("LEFT", "u1", "Alice", dt(2026, 1, 1, 10, 30), from_ch="A"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 2
    total = sum(s["duration_seconds"] for s in sessions)
    assert total == 1800  # 10:00 -> 10:30, still correct total despite the glitch


def test_missing_join_event_drops_the_orphaned_leave():
    """Documents the known limitation: a LEFT with no matching open session
    (event history starts mid-session) can't be reconstructed from events
    alone — this is exactly why live reconciliation now also OPENS sessions
    for people already present, not just closes them."""
    events = [
        ev("LEFT", "u1", "Alice", dt(2026, 1, 1, 10, 0), from_ch="A"),
    ]
    sessions = vs.reconstruct_sessions_with_ids(events)
    assert len(sessions) == 0  # correctly produces nothing — no fabricated duration


# ---------- 4. Long / still-open sessions — no artificial cap ----------

def test_long_session_not_capped():
    events = [
        ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 0, 0), to_ch="A"),
    ]
    now = dt(2026, 1, 1, 14, 0)  # 14 hours later, still no LEFT event
    sessions = vs.reconstruct_sessions_with_ids(events, now=now, close_open_at_now=True)
    assert len(sessions) == 1
    assert sessions[0]["duration_seconds"] == 14 * 3600  # NOT capped at 8h


def test_backfill_leaves_open_sessions_open_when_requested():
    events = [ev("JOINED", "u1", "Alice", dt(2026, 1, 1, 0, 0), to_ch="A")]
    sessions = vs.reconstruct_sessions_with_ids(events, close_open_at_now=False)
    assert len(sessions) == 1
    assert sessions[0]["end"] is None
    assert sessions[0]["duration_seconds"] is None


# ---------- 5. Bot restart / reconnect reconciliation (pure decision logic) ----------

def test_reconciliation_user_still_present_stays_open():
    """Scenario A: user joins, bot restarts, user stays in VC, bot comes back."""
    to_close, to_open = vs.compute_reconciliation_actions(
        open_session_user_ids={"u1"}, currently_present_user_ids={"u1"}
    )
    assert to_close == set()
    assert to_open == set()


def test_reconciliation_user_left_while_offline_gets_closed():
    """Scenario B: user joins, bot restarts, user leaves while bot is offline."""
    to_close, to_open = vs.compute_reconciliation_actions(
        open_session_user_ids={"u1"}, currently_present_user_ids=set()
    )
    assert to_close == {"u1"}
    assert to_open == set()


def test_reconciliation_user_already_in_vc_at_startup_gets_opened():
    """The audit's Critical/High finding: someone present with no open row must get one."""
    to_close, to_open = vs.compute_reconciliation_actions(
        open_session_user_ids=set(), currently_present_user_ids={"u1"}
    )
    assert to_close == set()
    assert to_open == {"u1"}


def test_reconciliation_mixed_case():
    """u1 legitimately still there, u2's session is stale, u3 is newly-discovered present."""
    to_close, to_open = vs.compute_reconciliation_actions(
        open_session_user_ids={"u1", "u2"}, currently_present_user_ids={"u1", "u3"}
    )
    assert to_close == {"u2"}
    assert to_open == {"u3"}


def test_multiple_restarts_idempotent():
    """Scenario D: running reconciliation twice in a row with no state change is a no-op both times."""
    open_ids, present_ids = {"u1"}, {"u1"}
    to_close_1, to_open_1 = vs.compute_reconciliation_actions(open_ids, present_ids)
    # simulate applying the (empty) actions, then reconciling again
    to_close_2, to_open_2 = vs.compute_reconciliation_actions(open_ids, present_ids)
    assert to_close_1 == to_close_2 == set()
    assert to_open_1 == to_open_2 == set()


# ---------- 6. Midnight crossing — the Critical bug from the audit ----------

def test_midnight_crossing_splits_correctly_between_today_and_yesterday():
    """The exact scenario from the audit: 23:00 yesterday -> 01:00 today.
    Today should get 1h, yesterday should get 1h — not 2h counted toward either."""
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 14, 23, 0), "end": dt(2026, 1, 15, 1, 0),
        "duration_seconds": 7200,
    }
    now = dt(2026, 1, 15, 12, 0)  # "now" is midday on the 15th

    today_start, today_end = vs.period_bounds("today", now=now, tz_offset_hours=0)
    yesterday_start, yesterday_end = vs.period_bounds("yesterday", now=now, tz_offset_hours=0)

    today_clipped = vs.clip_sessions_to_period([session], today_start, today_end, now=now)
    yesterday_clipped = vs.clip_sessions_to_period([session], yesterday_start, yesterday_end, now=now)

    assert len(today_clipped) == 1
    assert today_clipped[0]["duration_seconds"] == 3600  # exactly 1h (00:00-01:00), not 2h

    assert len(yesterday_clipped) == 1
    assert yesterday_clipped[0]["duration_seconds"] == 3600  # exactly 1h (23:00-00:00)

    # and the two together account for the whole session, with no overlap/double count
    assert today_clipped[0]["duration_seconds"] + yesterday_clipped[0]["duration_seconds"] == session["duration_seconds"]


def test_session_entirely_before_today_is_excluded_from_today():
    """A session that ended yesterday shouldn't leak into 'today' just because
    it's the most recent thing in the table (this was implicitly possible
    under the old end-time-only filter in some edge orderings)."""
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 14, 10, 0), "end": dt(2026, 1, 14, 11, 0),
        "duration_seconds": 3600,
    }
    now = dt(2026, 1, 15, 12, 0)
    today_start, today_end = vs.period_bounds("today", now=now, tz_offset_hours=0)
    clipped = vs.clip_sessions_to_period([session], today_start, today_end, now=now)
    assert clipped == []


# ---------- 7. Week / month boundary crossing ----------

def test_week_boundary_crossing():
    # Jan 12, 2026 is a Monday. A session starting Sunday night, ending Monday morning
    # should split across the week boundary.
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 11, 23, 0),   # Sunday 23:00 (previous week)
        "end": dt(2026, 1, 12, 1, 0),      # Monday 01:00 (new week)
        "duration_seconds": 7200,
    }
    now = dt(2026, 1, 12, 12, 0)
    week_start, week_end = vs.period_bounds("week", now=now, tz_offset_hours=0)
    assert week_start == dt(2026, 1, 12, 0, 0)  # week starts Monday

    clipped = vs.clip_sessions_to_period([session], week_start, week_end, now=now)
    assert len(clipped) == 1
    assert clipped[0]["duration_seconds"] == 3600  # only the 1h that's actually in the new week


def test_month_boundary_crossing():
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 31, 23, 0),
        "end": dt(2026, 2, 1, 1, 0),
        "duration_seconds": 7200,
    }
    now = dt(2026, 2, 1, 12, 0)
    month_start, month_end = vs.period_bounds("month", now=now, tz_offset_hours=0)
    clipped = vs.clip_sessions_to_period([session], month_start, month_end, now=now)
    assert len(clipped) == 1
    assert clipped[0]["duration_seconds"] == 3600  # only Feb's 1 hour, not the Jan hour too


def test_december_to_january_month_rollover():
    """Regression guard for the month+1 edge case around year boundaries."""
    now = dt(2026, 12, 15, 0, 0)
    start, end = vs.period_bounds("month", now=now, tz_offset_hours=0)
    assert start == dt(2026, 12, 1, 0, 0)
    assert end == dt(2027, 1, 1, 0, 0)


# ---------- 8. Timezone boundaries (IST +5:30 example from the audit) ----------

def test_timezone_shifts_the_day_boundary():
    """23:30 UTC on Jan 14 is already Jan 15, 05:00 IST — so under IST,
    a session at 23:30 UTC should count toward 'today' if 'now' is also
    evaluated in IST as the 15th."""
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 14, 23, 30),
        "end": dt(2026, 1, 14, 23, 45),
        "duration_seconds": 900,
    }
    now_utc = dt(2026, 1, 15, 1, 0)  # 06:30 IST on the 15th
    ist_offset = 5.5

    today_start, today_end = vs.period_bounds("today", now=now_utc, tz_offset_hours=ist_offset)
    clipped = vs.clip_sessions_to_period([session], today_start, today_end, now=now_utc)
    assert len(clipped) == 1
    assert clipped[0]["duration_seconds"] == 900  # counts fully toward IST "today"

    # Under UTC (no offset), the same session/now would NOT count toward "today"
    # at all, since in UTC it's still the 14th when the session happened.
    today_start_utc, today_end_utc = vs.period_bounds("today", now=now_utc, tz_offset_hours=0)
    clipped_utc = vs.clip_sessions_to_period([session], today_start_utc, today_end_utc, now=now_utc)
    assert clipped_utc == []


def test_all_period_is_unbounded():
    start, end = vs.period_bounds("all", now=dt(2026, 1, 1, 0, 0), tz_offset_hours=5.5)
    assert start is None
    assert end is None
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2020, 1, 1, 0, 0), "end": dt(2020, 1, 1, 1, 0), "duration_seconds": 3600,
    }
    clipped = vs.clip_sessions_to_period([session], start, end)
    assert len(clipped) == 1
    assert clipped[0]["duration_seconds"] == 3600  # ancient session still counts fully toward "all"


# ---------- 9. Still-open sessions inside a period query ----------

def test_still_open_session_clips_to_now_not_full_future():
    """A session with no end yet (user still in VC) should only count up to
    `now`, not somehow extend into the future relative to the period."""
    session = {
        "user_id": "u1", "user_name": "Alice", "channel_name": "A",
        "start": dt(2026, 1, 15, 9, 0), "end": None, "duration_seconds": None,
    }
    now = dt(2026, 1, 15, 10, 30)
    today_start, today_end = vs.period_bounds("today", now=now, tz_offset_hours=0)
    clipped = vs.clip_sessions_to_period([session], today_start, today_end, now=now)
    assert len(clipped) == 1
    assert clipped[0]["duration_seconds"] == 90 * 60  # 09:00 -> 10:30, not beyond


# ---------- 10. Aggregate stat correctness on clipped sessions ----------

def test_vc_time_totals_uses_clipped_duration():
    sessions = [
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 0, 0), "end": dt(2026, 1, 15, 1, 0), "duration_seconds": 3600},
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 2, 0), "end": dt(2026, 1, 15, 2, 30), "duration_seconds": 1800},
    ]
    totals = vs.vc_time_totals(sessions)
    assert totals[0]["user_id"] == "u1"
    assert totals[0]["total_seconds"] == 5400


def test_longest_session_per_user():
    sessions = [
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 0, 0), "end": dt(2026, 1, 15, 1, 0), "duration_seconds": 3600},
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 2, 0), "end": dt(2026, 1, 15, 5, 0), "duration_seconds": 10800},
    ]
    longest = vs.longest_sessions(sessions)
    assert longest[0]["duration_seconds"] == 10800


def test_peak_concurrent_simple_overlap():
    sessions = [
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 0, 0), "end": dt(2026, 1, 15, 1, 0), "duration_seconds": 3600},
        {"user_id": "u2", "user_name": "Bob", "channel_name": "A",
         "start": dt(2026, 1, 15, 0, 30), "end": dt(2026, 1, 15, 1, 30), "duration_seconds": 3600},
    ]
    peaks = vs.peak_concurrent_by_channel(sessions)
    assert peaks["A"] == 2  # both present between 00:30-01:00


def test_hour_of_day_totals_sums_to_total_duration():
    sessions = [
        {"user_id": "u1", "user_name": "Alice", "channel_name": "A",
         "start": dt(2026, 1, 15, 22, 0), "end": dt(2026, 1, 16, 1, 0), "duration_seconds": 10800},
    ]
    hours = vs.hour_of_day_totals(sessions)
    assert sum(hours) == 10800  # spans midnight but nothing lost across the day-bucket split


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
