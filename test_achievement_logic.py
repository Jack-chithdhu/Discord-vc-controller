"""Unit tests for achievement_logic.py (pure rules, no database)."""
from datetime import date, datetime, timedelta, timezone

import pytest

import achievement_logic as al

UTC = timezone.utc
TZ = 5.5                                    # IST, like production
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)   # = 17:30 local on 17 Oct


def dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def S(user, start, end, ch="100", est_s=False, est_e=False, name="Someone", ch_name="General"):
    return {"user_id": str(user), "user_name": name, "channel_id": ch, "channel_name": ch_name,
            "start": start, "end": end, "start_is_estimated": est_s, "end_is_estimated": est_e}


def ev(sessions, others=(), now=NOW, prior=None, minutes=15, cat=None, **kw):
    return al.evaluate_member("1", sessions, list(others), now, TZ, minutes, catalogue=cat,
                              prior_streak=prior, **kw)


def day_session(d, hours=1, start_h=4, user="1", **kw):
    """A precise session that starts at 04:00 UTC (09:30 local) on date d."""
    start = dt(d.year, d.month, d.day, start_h)
    return S(user, start, start + timedelta(hours=hours), **kw)


def eligible(e):
    return set(e.eligible_ids())


# ------------------------------------------------------------ catalogue
def test_default_catalogue_matches_the_agreed_values():
    got = {a.id: (a.metric, a.target, a.reward) for a in al.CATALOGUE}
    assert got == {
        "first_connection": ("qualifying_sessions", 1, 10),
        "regular": ("qualifying_sessions", 10, 25),
        "voice_veteran": ("lifetime_hours", 25, 50),
        "seven_day_run": ("longest_streak", 7, 30),
        "monthly_regular": ("best_month_days", 20, 75),
        "social_gamer": ("social_companions", 5, 40),
        "night_owl": ("night_hours", 10, 25),
        "dedicated": ("lifetime_hours", 100, 100),
    }


def test_catalogue_ids_are_unique_stable_slugs():
    ids = [a.id for a in al.CATALOGUE]
    assert len(ids) == len(set(ids))
    assert all(i == i.lower() and " " not in i for i in ids)
    assert all(a.metric in al.evaluate_member("1", [], [], NOW, TZ).metrics for a in al.CATALOGUE)


def test_overrides_change_reward_target_enabled_and_ignore_garbage():
    cat = al.effective_catalogue({
        "regular": {"reward": 99, "target": 3},
        "dedicated": {"enabled": False},
        "night_owl": {"reward": -5, "target": 0},        # out of range -> ignored
        "voice_veteran": {"reward": "lots", "target": True},
        "nope": {"reward": 1},
    })
    by = al.catalogue_by_id(cat)
    assert (by["regular"].reward, by["regular"].target) == (99, 3)
    assert by["dedicated"].enabled is False
    assert (by["night_owl"].reward, by["night_owl"].target) == (25, 10)
    assert (by["voice_veteran"].reward, by["voice_veteran"].target) == (50, 25)
    assert [a.order for a in cat] == sorted(a.order for a in cat)


def test_settings_helpers():
    assert al.clamp_daily_minutes(1) == 5 and al.clamp_daily_minutes(9999) == 120
    assert al.clamp_daily_minutes("20") == 20 and al.clamp_daily_minutes("x") == 15
    assert al.parse_bool("on") and al.parse_bool("TRUE") and not al.parse_bool("off")
    assert al.parse_bool(None, True) is True


# ------------------------------------------------------------ sessions & dates
def test_empty_history():
    e = ev([])
    assert e.metrics["qualifying_sessions"] == 0 and e.metrics["lifetime_hours"] == 0
    assert e.streak == al.StreakState() and e.metrics["current_streak"] == 0
    assert eligible(e) == set()
    assert all(p["fraction"] == 0 for p in e.progress)


def test_one_valid_session_unlocks_first_connection_only():
    e = ev([day_session(date(2026, 10, 16))])
    assert eligible(e) == {"first_connection"}
    assert e.metrics["qualifying_sessions"] == 1
    assert e.streak == al.StreakState(1, 1, date(2026, 10, 16))


def test_short_session_does_not_qualify_but_counts_for_hours():
    e = ev([S(1, dt(2026, 10, 16, 4), dt(2026, 10, 16, 4, 10))])       # 10 min < 15
    assert e.metrics["qualifying_sessions"] == 0 and eligible(e) == set()
    assert e.metrics["lifetime_hours"] == pytest.approx(10 / 60, abs=1e-3)
    assert e.streak.longest == 0                                       # day did not reach 15 min


def test_threshold_is_configurable_and_bounded():
    s = [S(1, dt(2026, 10, 16, 4), dt(2026, 10, 16, 4, 10))]
    assert ev(s, minutes=10).streak.longest == 1
    assert ev(s, minutes=1).streak.longest == 1          # clamps to 5 min
    assert ev(s, minutes=9999).streak.longest == 0       # clamps to 120 min


def test_several_short_sessions_same_day_combine_into_one_qualifying_day():
    e = ev([S(1, dt(2026, 10, 16, 4, i * 20), dt(2026, 10, 16, 4, i * 20 + 8)) for i in range(3)])   # 3 x 8 min
    assert e.streak == al.StreakState(1, 1, date(2026, 10, 16))
    assert e.metrics["qualifying_sessions"] == 0         # but none is a qualifying SESSION on its own


def test_blips_never_count():
    blips = [S(1, dt(2026, 10, 16, 4, i), dt(2026, 10, 16, 4, i) + timedelta(seconds=50)) for i in range(40)]
    e = ev(blips)
    assert e.metrics["lifetime_hours"] == 0 and e.streak.longest == 0 and eligible(e) == set()


def test_session_crossing_local_midnight_splits_across_two_dates():
    # 18:00-20:30 UTC = 23:30-02:00 local -> 30 min on the 16th, 120 min on the 17th? (17th local is today)
    e = ev([S(1, dt(2026, 10, 16, 18, 0), dt(2026, 10, 16, 20, 30))])
    assert e.dates == [date(2026, 10, 16), date(2026, 10, 17)]
    assert e.streak == al.StreakState(2, 2, date(2026, 10, 17))


def test_midnight_split_where_one_side_is_too_short():
    # 18:20-19:40 UTC = 23:50-01:10 local: 10 min on the 16th (<15) and 70 min on the 17th
    e = ev([S(1, dt(2026, 10, 16, 18, 20), dt(2026, 10, 16, 19, 40))])
    assert e.dates == [date(2026, 10, 17)]


def test_local_date_differs_from_utc_date():
    # 20:00 UTC on 15 Oct is 01:30 local on the 16th -> qualifies the 16th, not the 15th
    e = ev([S(1, dt(2026, 10, 15, 20), dt(2026, 10, 15, 21))])
    assert e.dates == [date(2026, 10, 16)]


def test_month_and_year_boundary():
    now = dt(2027, 1, 2, 12)
    e = ev([day_session(date(2026, 12, 31)), day_session(date(2027, 1, 1))], now=now)
    assert e.dates == [date(2026, 12, 31), date(2027, 1, 1)]
    assert e.streak.longest == 2
    assert al.month_counts(e.dates) == {(2026, 12): 1, (2027, 1): 1}
    assert e.metrics["best_month_days"] == 1                           # months are not merged


def test_open_session_counts_for_lifetime_only_until_it_is_closed():
    e = ev([S(1, dt(2026, 10, 17, 9), None)])                          # open since 3 h ago
    assert e.metrics["lifetime_hours"] == pytest.approx(3.0, abs=1e-3)
    assert e.metrics["qualifying_sessions"] == 1
    assert e.streak == al.StreakState() and e.dates == []              # no streak credit yet


def test_invalid_and_negative_durations_are_ignored():
    rows = [S(1, dt(2026, 10, 16, 5), dt(2026, 10, 16, 4)),            # negative
            S(1, dt(2026, 10, 16, 5), dt(2026, 10, 16, 5)),            # zero
            S(1, dt(2026, 10, 18, 5), dt(2026, 10, 18, 7)),            # starts in the future
            {"user_id": "1", "start": None, "end": None}]              # no start
    e = ev(rows)
    assert e.metrics["lifetime_hours"] == 0 and eligible(e) == set()


def test_stored_duration_column_is_never_trusted():
    row = S(1, dt(2026, 10, 16, 4), dt(2026, 10, 16, 5))
    row["duration_seconds"] = 999999
    assert ev([row]).metrics["lifetime_hours"] == pytest.approx(1.0)


def test_session_end_after_now_is_clipped_to_now():
    e = ev([S(1, dt(2026, 10, 17, 11), dt(2026, 10, 17, 20))])
    assert e.metrics["lifetime_hours"] == pytest.approx(1.0, abs=1e-3)


def test_estimated_timestamps_follow_the_documented_policy():
    est_start = day_session(date(2026, 10, 14), est_s=True)
    est_end = day_session(date(2026, 10, 15), est_e=True)
    good = day_session(date(2026, 10, 16))
    e = ev([est_start, est_end, good])
    assert e.metrics["qualifying_sessions"] == 3                       # lifetime stats accept them
    assert e.metrics["lifetime_hours"] == pytest.approx(3.0)
    assert e.dates == [date(2026, 10, 16)]                             # but only the precise one makes a day
    assert e.streak == al.StreakState(1, 1, date(2026, 10, 16))
    assert e.metrics["night_hours"] == 0


def test_missing_user_names_and_channel_info_do_not_break_anything():
    row = S(1, dt(2026, 10, 16, 4), dt(2026, 10, 16, 5), name=None, ch=None, ch_name=None)
    e = ev([row])
    assert e.streak.longest == 1 and e.metrics["social_companions"] == 0


def test_other_users_rows_in_the_sessions_list_are_ignored():
    e = ev([day_session(date(2026, 10, 16), user="1"), day_session(date(2026, 10, 15), user="2")])
    assert e.dates == [date(2026, 10, 16)]


def test_naive_and_iso_string_timestamps_are_accepted():
    row = S(1, "2026-10-16T04:00:00Z", "2026-10-16T05:00:00+00:00")
    assert ev([row]).streak.longest == 1


# ------------------------------------------------------------ streaks
def D(day):
    return date(2026, 10, day)


def test_first_day_starts_at_one_and_same_day_sessions_do_not_increment():
    s = al.advance_streak(None, [D(10), D(10), D(10)], D(17))
    assert s == al.StreakState(1, 1, D(10))


def test_consecutive_dates_increment():
    assert al.advance_streak(None, [D(10), D(11), D(12)], D(17)) == al.StreakState(3, 3, D(12))


def test_missed_date_resets_current_but_keeps_longest():
    s = al.advance_streak(None, [D(1), D(2), D(3), D(4), D(6), D(7)], D(17))
    assert s == al.StreakState(2, 4, D(7))


def test_future_dates_never_count():
    assert al.advance_streak(None, [D(16), D(17), D(18), D(19)], D(17)) == al.StreakState(2, 2, D(17))


def test_late_arriving_older_date_does_not_inflate_the_streak():
    saved = al.advance_streak(None, [D(1), D(2), D(4), D(5)], D(5))     # gap on the 3rd
    assert saved == al.StreakState(2, 2, D(5))
    # a repaired session now shows the 3rd was qualifying; recorded history is not rewritten
    again = al.advance_streak(saved, [D(1), D(2), D(3), D(4), D(5)], D(5))
    assert again == saved


def test_new_date_after_saved_state_continues_or_restarts():
    saved = al.StreakState(3, 5, D(10))
    assert al.advance_streak(saved, [D(11)], D(17)) == al.StreakState(4, 5, D(11))
    assert al.advance_streak(saved, [D(13)], D(17)) == al.StreakState(1, 5, D(13))
    assert al.advance_streak(saved, [D(11), D(12), D(13), D(14), D(15), D(16)], D(17)).longest == 9


def test_repeated_evaluation_is_idempotent():
    dates = [D(1), D(2), D(3), D(9), D(10)]
    once = al.advance_streak(None, dates, D(17))
    assert al.advance_streak(once, dates, D(17)) == once
    sessions = [day_session(date(2026, 10, d)) for d in (14, 15, 16)]
    first = ev(sessions)
    second = ev(sessions, prior=first.streak)
    assert second.streak == first.streak and second.progress == first.progress


def test_restart_reload_gives_same_result_as_continuous_run():
    """Evaluating day by day (state saved/reloaded between runs) equals one full evaluation."""
    all_days = [14, 15, 16, 17]
    sessions = [day_session(date(2026, 10, d)) for d in all_days]
    state = None
    for d in all_days:
        now = dt(2026, 10, d, 12)
        state = ev([s for s in sessions if s["start"] <= now], now=now, prior=state).streak
    full = ev(sessions)
    assert state == full.streak == al.StreakState(4, 4, D(17))


def test_effective_current_expires_after_a_missed_day():
    s = al.StreakState(5, 5, D(10))
    assert al.effective_current(s, D(10)) == 5
    assert al.effective_current(s, D(11)) == 5          # today can still extend it
    assert al.effective_current(s, D(12)) == 0
    assert al.effective_current(None, D(12)) == 0
    assert al.effective_current(al.StreakState(), D(12)) == 0


def test_longest_survives_a_reset_in_the_evaluator():
    old = [day_session(date(2026, 10, d)) for d in (1, 2, 3, 4, 5)]
    new = [day_session(date(2026, 10, 16))]
    e = ev(old + new)
    assert e.metrics["longest_streak"] == 5 and e.metrics["current_streak"] == 1


# ------------------------------------------------------------ thresholds, one by one
def n_days(count, start=date(2026, 9, 1), **kw):
    return [day_session(start + timedelta(days=i), **kw) for i in range(count)]


def test_first_connection_and_regular_thresholds():
    nine = [S(1, dt(2026, 9, 1 + i, 4), dt(2026, 9, 1 + i, 5)) for i in range(9)]
    assert "regular" not in eligible(ev(nine)) and "first_connection" in eligible(ev(nine))
    ten = nine + [S(1, dt(2026, 9, 20, 4), dt(2026, 9, 20, 5))]
    assert {"first_connection", "regular"} <= eligible(ev(ten))


def test_hour_milestones_voice_veteran_and_dedicated():
    def with_hours(h):
        return [S(1, dt(2026, 10, 1, 0), dt(2026, 10, 1, 0) + timedelta(hours=h))]
    assert "voice_veteran" not in eligible(ev(with_hours(24)))
    assert "voice_veteran" in eligible(ev(with_hours(25)))
    assert "dedicated" not in eligible(ev(with_hours(99)))
    assert "dedicated" in eligible(ev(with_hours(100))) and "voice_veteran" in eligible(ev(with_hours(100)))


def test_seven_day_run_threshold():
    assert "seven_day_run" not in eligible(ev(n_days(6, date(2026, 10, 10))))
    assert "seven_day_run" in eligible(ev(n_days(7, date(2026, 10, 10))))


def test_seven_day_run_needs_consecutive_days():
    days = [day_session(date(2026, 10, d)) for d in (1, 2, 3, 4, 5, 6, 8)]     # gap on the 7th
    assert "seven_day_run" not in eligible(ev(days))


def test_monthly_regular_threshold_is_per_calendar_month():
    nineteen = [day_session(date(2026, 9, d)) for d in range(1, 20)]
    twenty = nineteen + [day_session(date(2026, 9, 25))]
    assert "monthly_regular" not in eligible(ev(nineteen))
    assert "monthly_regular" in eligible(ev(twenty))
    spread = [day_session(date(2026, 8, d)) for d in range(15, 31)] + [day_session(date(2026, 9, d)) for d in range(1, 11)]
    assert "monthly_regular" not in eligible(ev(spread))                          # 16 + 10 days across two months


def test_progress_is_reported_for_locked_achievements():
    e = ev([S(1, dt(2026, 9, 1 + i, 4), dt(2026, 9, 1 + i, 5)) for i in range(4)])
    p = {x["id"]: x for x in e.progress}
    assert p["regular"]["value"] == 4 and p["regular"]["target"] == 10 and p["regular"]["fraction"] == pytest.approx(0.4)
    assert p["regular"]["eligible"] is False and p["first_connection"]["eligible"] is True
    assert p["dedicated"]["fraction"] == pytest.approx(0.04)


def test_disabled_achievement_is_never_eligible_but_still_has_progress():
    cat = al.effective_catalogue({"first_connection": {"enabled": False}})
    e = ev([day_session(date(2026, 10, 16))], cat=cat)
    assert "first_connection" not in eligible(e)
    assert {x["id"]: x for x in e.progress}["first_connection"]["value"] == 1


# ------------------------------------------------------------ night owl
def test_night_owl_counts_only_time_inside_the_window():
    # 17:00-19:00 UTC = 22:30-00:30 local -> 1.5 h in window (22:30-24:00 + 00:00-00:30 = 2h) -> compute exactly
    e = ev([S(1, dt(2026, 10, 16, 17, 0), dt(2026, 10, 16, 19, 0))])
    assert e.metrics["night_hours"] == pytest.approx(2.0)


def test_night_owl_splits_across_midnight_and_stops_at_four():
    # 20:00 UTC (01:30 local) to 00:00 UTC (05:30 local): only 01:30-04:00 = 2.5 h counts
    e = ev([S(1, dt(2026, 10, 15, 20, 0), dt(2026, 10, 16, 0, 0))])
    assert e.metrics["night_hours"] == pytest.approx(2.5)


def test_night_owl_is_not_decided_by_session_start_alone():
    # starts at 21:00 local (before the window) but runs to 23:00 local -> only 1 h counts
    e = ev([S(1, dt(2026, 10, 16, 15, 30), dt(2026, 10, 16, 17, 30))])
    assert e.metrics["night_hours"] == pytest.approx(1.0)


def test_night_owl_unlocks_at_ten_hours_and_ignores_uncertain_sessions():
    def nights(count, **kw):   # 22:30-02:30 local would be 4h; use 2h/night inside the window
        return [S(1, dt(2026, 9, 1 + i, 17, 0), dt(2026, 9, 1 + i, 19, 0), **kw) for i in range(count)]
    assert "night_owl" not in eligible(ev(nights(4)))            # 8 h
    assert "night_owl" in eligible(ev(nights(5)))               # 10 h
    assert "night_owl" not in eligible(ev(nights(5, est_e=True)))
    assert "night_owl" not in eligible(ev(nights(5, est_s=True)))


# ------------------------------------------------------------ social gamer
def together(user, ch, start, hours=1.0, **kw):
    return S(user, start, start + timedelta(hours=hours), ch=ch, **kw)


def test_shared_time_requires_real_overlap():
    base = dt(2026, 10, 16, 4)
    mine = [together(1, "100", base)]
    other = [together(2, "100", base + timedelta(hours=1))]      # starts as I leave: zero overlap
    assert al.evaluate_member("1", mine, other, NOW, TZ).metrics["social_companions"] == 0
    other = [together(2, "100", base, hours=1)]
    assert al.shared_time("1", al.precise_sessions(al.normalise_sessions(mine + other, NOW))) == {"2": 3600.0}


def test_partial_overlap_is_clipped_exactly():
    base = dt(2026, 10, 16, 4)
    mine = [together(1, "100", base, hours=2)]
    other = [together(2, "100", base + timedelta(minutes=90), hours=2)]
    sess = al.precise_sessions(al.normalise_sessions(mine + other, NOW))
    assert al.shared_time("1", sess) == {"2": 1800.0}


def test_different_channels_do_not_count_as_together():
    base = dt(2026, 10, 16, 4)
    sess = al.precise_sessions(al.normalise_sessions(
        [together(1, "100", base), together(2, "200", base)], NOW))
    assert al.shared_time("1", sess) == {}


def test_renamed_channel_still_matches_by_id():
    base = dt(2026, 10, 16, 4)
    a = together(1, "100", base, ch_name="Old name")
    b = together(2, "100", base, ch_name="New name")
    sess = al.precise_sessions(al.normalise_sessions([a, b], NOW))
    assert al.shared_time("1", sess) == {"2": 3600.0}


def friends(count, hours=1.0, start=dt(2026, 10, 10, 4), **kw):
    return [together(100 + i, "100", start + timedelta(days=0), hours, **kw) for i in range(count)]


def test_social_gamer_needs_five_distinct_members_with_sixty_minutes_each():
    me = [together(1, "100", dt(2026, 10, 10, 4), hours=1)]
    assert "social_gamer" not in eligible(ev(me, friends(4)))
    assert "social_gamer" in eligible(ev(me, friends(5)))
    short = friends(5, hours=0.9)                                # 54 min each
    assert "social_gamer" not in eligible(ev(me, short))


def test_one_companion_across_many_sessions_counts_once():
    me = [together(1, "100", dt(2026, 10, 1 + i, 4), hours=1) for i in range(6)]
    pal = [together(2, "100", dt(2026, 10, 1 + i, 4), hours=1) for i in range(6)]      # 6 h with ONE person
    e = ev(me, pal)
    assert e.metrics["social_companions"] == 1 and "social_gamer" not in eligible(e)


def test_time_summed_across_sessions_can_reach_the_threshold():
    me = [together(1, "100", dt(2026, 10, 1 + i, 4), hours=0.5) for i in range(2)]
    pal = [together(2, "100", dt(2026, 10, 1 + i, 4), hours=0.5) for i in range(2)]
    one = al.evaluate_member("1", me, pal, NOW, TZ)
    assert one.metrics["social_companions"] == 1                      # 60 min summed over 2 sessions = 1 member
    assert "social_gamer" not in eligible(one)
    many = [together(100 + k, "100", dt(2026, 10, 1 + i, 4), hours=0.5) for k in range(5) for i in range(2)]
    assert "social_gamer" in eligible(ev(me, many))


def test_bots_and_self_are_never_companions():
    me = [together(1, "100", dt(2026, 10, 10, 4))]
    others = friends(5)
    e = ev(me + [together(1, "100", dt(2026, 10, 10, 4))], others, bot_ids={"100", "101"})
    assert e.metrics["social_companions"] == 3                     # 2 of the 5 are bots; self ignored


def test_uncertain_sessions_do_not_count_for_shared_time():
    me = [together(1, "100", dt(2026, 10, 10, 4))]
    est = friends(5, est_e=True)
    assert ev(me, est).metrics["social_companions"] == 0
    assert ev([together(1, "100", dt(2026, 10, 10, 4), est_s=True)], friends(5)).metrics["social_companions"] == 0


def test_open_sessions_do_not_count_for_shared_time():
    me = [together(1, "100", dt(2026, 10, 10, 4))]
    still_open = [S(100 + i, dt(2026, 10, 10, 4), None, ch="100") for i in range(5)]
    assert ev(me, still_open).metrics["social_companions"] == 0


# ------------------------------------------------------------ calendar safety
def test_unsafe_timezone_blocks_calendar_metrics_only():
    sessions = n_days(7, date(2026, 10, 10)) + [S(1, dt(2026, 9, 1 + i, 17), dt(2026, 9, 1 + i, 19)) for i in range(5)]
    safe = ev(sessions)
    assert {"seven_day_run", "night_owl"} <= eligible(safe)
    unsafe = ev(sessions, calendar_ok=False)
    assert {"seven_day_run", "night_owl", "monthly_regular"}.isdisjoint(eligible(unsafe))
    assert "first_connection" in eligible(unsafe)
    assert {p["id"] for p in unsafe.progress if p["blocked"]} == {"seven_day_run", "monthly_regular", "night_owl"}
    saved = al.StreakState(3, 9, D(5))
    assert ev(sessions, prior=saved, calendar_ok=False).streak == saved       # untouched


# ------------------------------------------------------------ presentation
def test_progress_bar_and_text():
    assert al.progress_bar(0) == "▱" * 10 and al.progress_bar(1) == "▰" * 10
    assert al.progress_bar(0.5).count("▰") == 5 and al.progress_bar(5) == "▰" * 10
    a = al.catalogue_by_id()["voice_veteran"]
    assert al.progress_text(a, 12.5) == "12.5/25 hours" and al.progress_text(a, 80) == "25/25 hours"


def test_achievement_lines_group_by_status_and_show_rewards_only_when_applicable():
    cat = al.effective_catalogue({"dedicated": {"reward": 0}})
    e = ev([day_session(date(2026, 10, 16))], cat=cat)
    lines = al.achievement_lines(cat, e.progress, {"first_connection"})
    assert len(lines["unlocked"]) == 1 and "First Connection" in lines["unlocked"][0]
    text = "\n".join(lines["in_progress"] + lines["not_started"])
    assert "Dedicated" in text and "+0 pts" not in text and "+30 pts" in text
    assert "+10 pts" not in "\n".join(lines["unlocked"])
    off = al.achievement_lines(cat, e.progress, {"first_connection"}, rewards_enabled=False)
    assert "pts" not in "\n".join(off["in_progress"] + off["not_started"])


def test_regular_is_in_progress_after_one_session():
    e = ev([day_session(date(2026, 10, 16))])
    lines = al.achievement_lines(al.effective_catalogue(), e.progress, {"first_connection"})
    assert any("Regular" in l for l in lines["in_progress"])
    assert any("Dedicated" in l for l in lines["in_progress"])               # 1 h of 100 h has started
    assert any("Social Gamer" in l for l in lines["not_started"])


def test_disabled_achievements_are_hidden_unless_already_unlocked():
    cat = al.effective_catalogue({"dedicated": {"enabled": False}})
    e = ev([], cat=cat)
    assert "Dedicated" not in "".join(sum(al.achievement_lines(cat, e.progress, set()).values(), []))
    assert "Dedicated" in "".join(sum(al.achievement_lines(cat, e.progress, {"dedicated"}).values(), []))


def test_all_lines_fit_inside_one_embed():
    e = ev([])
    lines = al.achievement_lines(al.effective_catalogue(), e.progress, set())
    assert len("\n".join(sum(lines.values(), []))) < 3500


def test_next_streak_goal():
    cat = al.effective_catalogue()
    e = ev(n_days(3, date(2026, 10, 14)))
    goal, prog = al.next_streak_goal(cat, e.progress, set())
    assert goal.id == "seven_day_run" and prog["value"] == 3
    assert al.next_streak_goal(cat, e.progress, {"seven_day_run"}) is None


def test_clip_text():
    assert al.clip_text("short", 100) == "short"
    long = "\n".join(["line"] * 500)
    out = al.clip_text(long, 100)
    assert len(out) <= 100 and out.endswith("…")


def test_parse_toggle_accepts_on_off_words_and_rejects_the_rest():
    for yes in ("on", "ON", " true ", "yes", "1", "enable", "Enabled"):
        assert al.parse_toggle(yes) is True, yes
    for no in ("off", "False", "no", "0", "disable", "DISABLED"):
        assert al.parse_toggle(no) is False, no
    for junk in (None, "", "maybe", "onn", "2"):
        assert al.parse_toggle(junk) is None, junk
