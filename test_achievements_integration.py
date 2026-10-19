"""
Integration tests for Member Achievements & Streaks against a REAL PostgreSQL.
Skipped unless TEST_DATABASE_URL is set (e.g. postgresql://postgres:pw@localhost/vct).
No Discord connection: bot objects are tiny fakes. The business logic itself is
never mocked - sessions are written to vc_sessions and evaluated for real.
"""
import importlib.util, os, sys, threading
from datetime import datetime, timedelta, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = timezone.utc


def _load_app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_ach_under_test", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app():
    mod = _load_app()
    assert mod.init_db()
    return mod


@pytest.fixture(autouse=True)
def clean(app):
    with app._cursor() as cur:
        cur.execute("DELETE FROM achievement_rewards")
        cur.execute("DELETE FROM member_achievements")
        cur.execute("DELETE FROM member_streaks")
        cur.execute("DELETE FROM vc_sessions")
        cur.execute("DELETE FROM points")
        cur.execute("DELETE FROM bot_settings WHERE key LIKE 'achievements\\_%'")
    app._ach_leaderboard_cache.clear()
    saved_bot = app.bot
    yield
    app.bot = saved_bot


def now_utc():
    return datetime.now(UTC)


def add_session(app, user, start, end, channel="100", est_s=False, est_e=False, name="Member"):
    with app._cursor() as cur:
        cur.execute(
            "INSERT INTO vc_sessions (user_id, user_name, channel_id, channel_name, started_at, ended_at, "
            "start_is_estimated, end_is_estimated, duration_seconds) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(user), name, channel, "General", start, end, est_s, est_e,
             None if end is None else (end - start).total_seconds()),
        )


def good_day(app, user, days_ago, hours=1.0, channel="100"):
    """A precise session safely inside one LOCAL day (10:00 local), `days_ago` local days back.
    Anchored to the local calendar, so the tests give the same answer at any time of day."""
    local_today = (now_utc() + timedelta(hours=5.5)).date()
    d = local_today - timedelta(days=days_ago)
    base = datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(hours=5.5) + timedelta(hours=10)
    add_session(app, user, base, base + timedelta(hours=hours), channel)


def balance(app, user):
    return app.get_balance(int(user))


def unlocked(app, user):
    with app._cursor() as cur:
        cur.execute("SELECT achievement_id FROM member_achievements WHERE user_id=%s", (str(user),))
        return {r["achievement_id"] for r in cur.fetchall()}


def count(app, table, user=None):
    with app._cursor() as cur:
        if user is None:
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        else:
            cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=%s", (str(user),))
        return cur.fetchone()["n"]


def ach(app, aid):
    return app.al.catalogue_by_id()[aid]


# ------------------------------------------------------------ schema
def test_schema_is_created_and_init_is_repeatable(app):
    assert app.init_db() and app.init_db()
    with app._cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = {r["table_name"] for r in cur.fetchall()}
        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='public'")
        idx = {r["indexname"] for r in cur.fetchall()}
    assert {"member_achievements", "member_streaks", "achievement_rewards", "points", "vc_sessions"} <= tables
    assert {"idx_member_ach_unlocked", "idx_member_ach_achievement", "idx_vc_sessions_channel_started"} <= idx
    assert "member_achievements_pkey" in idx and "achievement_rewards_user_id_achievement_id_key" in idx


def test_existing_points_functions_still_work(app):
    app.add_balance(5, 40)
    app.add_balance(5, -15)
    assert app.get_balance(5) == 25


# ------------------------------------------------------------ unlock + reward
def test_first_session_unlocks_first_connection_and_pays_once(app):
    good_day(app, 11, 1)
    r = app.ach_evaluate_member(11)
    assert [n["id"] for n in r["new"]] == ["first_connection"]
    assert balance(app, 11) == 10
    assert count(app, "achievement_rewards", 11) == 1
    with app._cursor() as cur:
        cur.execute("SELECT reward_amount, progress_at_unlock FROM member_achievements WHERE user_id='11'")
        row = cur.fetchone()
    assert row["reward_amount"] == 10 and row["progress_at_unlock"] == 1


def test_reevaluating_never_pays_again(app):
    good_day(app, 12, 1)
    for _ in range(5):
        app.ach_evaluate_member(12)
    assert balance(app, 12) == 10 and unlocked(app, 12) == {"first_connection"}
    assert count(app, "achievement_rewards", 12) == 1


def test_existing_balance_is_added_to_not_overwritten(app):
    app.add_balance(13, 500)
    good_day(app, 13, 1)
    app.ach_evaluate_member(13)
    assert balance(app, 13) == 510


def test_concurrent_claims_pay_exactly_once(app):
    a = ach(app, "regular")
    results, barrier = [], threading.Barrier(12)

    def worker():
        barrier.wait()
        results.append(app.ach_claim_unlock(14, a, 10, True))

    threads = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(1 for r in results if r["unlocked"]) == 1
    assert sum(r["paid"] for r in results) == 25
    assert balance(app, 14) == 25 and count(app, "achievement_rewards", 14) == 1


def test_concurrent_full_evaluations_pay_exactly_once(app):
    for d in range(1, 4):
        good_day(app, 15, d)
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        app.ach_evaluate_member(15)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert unlocked(app, 15) == {"first_connection"}
    assert balance(app, 15) == 10 and count(app, "achievement_rewards", 15) == 1


def test_failure_after_unlock_rolls_back_unlock_ledger_and_points(app, monkeypatch):
    a = ach(app, "regular")

    def boom(cur, uid, amount):
        raise RuntimeError("balance update failed")

    monkeypatch.setattr(app, "_add_balance_cur", boom)
    with pytest.raises(RuntimeError):
        app.ach_claim_unlock(16, a, 10, True)
    monkeypatch.undo()
    assert unlocked(app, 16) == set() and count(app, "achievement_rewards", 16) == 0 and balance(app, 16) == 0
    assert app.ach_claim_unlock(16, a, 10, True) == {"unlocked": True, "paid": 25}   # retry works
    assert balance(app, 16) == 25


def test_failure_inside_evaluation_leaves_nothing_half_done(app, monkeypatch):
    good_day(app, 17, 1)
    monkeypatch.setattr(app, "_add_balance_cur", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        app.ach_evaluate_member(17)
    monkeypatch.undo()
    assert unlocked(app, 17) == set() and balance(app, 17) == 0
    app.ach_evaluate_member(17)
    assert unlocked(app, 17) == {"first_connection"} and balance(app, 17) == 10


def test_zero_point_achievement_unlocks_without_ledger_or_points(app):
    app.ach_set_override("first_connection", "reward", 0)
    good_day(app, 18, 1)
    r = app.ach_evaluate_member(18)
    assert unlocked(app, 18) == {"first_connection"} and r["new"][0]["paid"] == 0
    assert balance(app, 18) == 0 and count(app, "achievement_rewards", 18) == 0


def test_disabled_rewards_still_unlock_badges_and_are_not_paid_later(app):
    app.ach_set_setting("rewards_enabled", "off")
    good_day(app, 19, 1)
    app.ach_evaluate_member(19)
    assert unlocked(app, 19) == {"first_connection"} and balance(app, 19) == 0
    app.ach_set_setting("rewards_enabled", "on")
    app.ach_evaluate_member(19)
    assert balance(app, 19) == 0 and count(app, "achievement_rewards", 19) == 0     # no retroactive payout


def test_disabled_achievement_cannot_be_newly_unlocked(app):
    app.ach_set_override("first_connection", "enabled", False)
    good_day(app, 20, 1)
    app.ach_evaluate_member(20)
    assert unlocked(app, 20) == set() and balance(app, 20) == 0
    app.ach_set_override("first_connection", "enabled", True)
    app.ach_evaluate_member(20)
    assert unlocked(app, 20) == {"first_connection"}


def test_existing_unlock_survives_the_achievement_being_disabled(app):
    good_day(app, 21, 1)
    app.ach_evaluate_member(21)
    app.ach_set_override("first_connection", "enabled", False)
    app.ach_evaluate_member(21)
    assert unlocked(app, 21) == {"first_connection"} and balance(app, 21) == 10


def test_feature_switch_off_does_nothing_and_back_on_catches_up(app):
    app.ach_set_setting("enabled", "off")
    good_day(app, 22, 1)
    assert app.ach_evaluate_member(22)["disabled"] is True
    assert count(app, "member_streaks", 22) == 0 and unlocked(app, 22) == set()
    app.ach_set_setting("enabled", "on")
    app.ach_evaluate_member(22)
    assert unlocked(app, 22) == {"first_connection"}


def test_reward_amount_override_is_used(app):
    app.ach_set_override("first_connection", "reward", 77)
    good_day(app, 23, 1)
    app.ach_evaluate_member(23)
    assert balance(app, 23) == 77


def test_bad_overrides_are_rejected(app):
    for args in [("nope", "reward", 1), ("regular", "color", 1), ("regular", "reward", -1),
                 ("regular", "reward", 10 ** 9), ("regular", "target", 0), ("regular", "enabled", "yes")]:
        with pytest.raises(ValueError):
            app.ach_set_override(*args)


# ------------------------------------------------------------ eligibility from real sessions
def test_estimated_and_open_sessions_follow_the_policy(app):
    now = now_utc()
    base = (now - timedelta(days=2)).replace(hour=5, minute=0, second=0, microsecond=0)
    add_session(app, 24, base, base + timedelta(hours=2), est_e=True)          # lifetime only
    add_session(app, 24, now - timedelta(hours=1), None)                        # open
    r = app.ach_evaluate_member(24)
    ev = r["evaluation"]
    assert ev.metrics["qualifying_sessions"] == 2 and ev.metrics["qualifying_dates"] == 0
    assert ev.streak.longest == 0


def test_seven_consecutive_days_unlock_seven_day_run(app):
    for d in range(1, 8):
        good_day(app, 25, d)
    app.ach_evaluate_member(25)
    assert {"first_connection", "seven_day_run"} <= unlocked(app, 25)
    assert balance(app, 25) == 10 + 30


def test_social_gamer_from_real_overlap_in_the_same_channel(app):
    base = (now_utc() - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    add_session(app, 26, base, base + timedelta(hours=1))
    for f in range(100, 105):
        add_session(app, f, base, base + timedelta(hours=1))
    app.ach_evaluate_member(26)
    assert "social_gamer" in unlocked(app, 26)
    add_session(app, 27, base, base + timedelta(hours=1), channel="999")        # another channel
    app.ach_evaluate_member(27)
    assert "social_gamer" not in unlocked(app, 27)


def test_bots_never_count_as_companions_or_get_evaluated(app):
    class M:
        def __init__(self, i, is_bot): self.id, self.bot = i, is_bot

    class G:
        members = [M(100 + i, True) for i in range(5)] + [M(26, False)]
        def get_member(self, i): return next((m for m in self.members if m.id == i), None)

    class B:
        def get_guild(self, _): return G()

    app.bot = B()
    base = (now_utc() - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    add_session(app, 26, base, base + timedelta(hours=1))
    for f in range(100, 105):
        add_session(app, f, base, base + timedelta(hours=1))
    app.ach_evaluate_member(26)
    assert "social_gamer" not in unlocked(app, 26)
    r = app.ach_evaluate_member(100)
    assert r.get("bot") is True and count(app, "member_streaks", 100) == 0


# ------------------------------------------------------------ streak persistence
def test_streak_is_saved_and_survives_a_restart(app):
    for d in (3, 2, 1):
        good_day(app, 30, d)
    app.ach_evaluate_member(30)
    row = app.ach_load_streak_row(30)
    assert (row["current_streak"], row["longest_streak"]) == (3, 3)
    data = app.ach_member_data(30)            # a fresh read, as after a restart
    assert data["streak"]["current"] == 3 and data["streak"]["longest"] == 3


def test_streak_continues_from_saved_state_and_resets_after_a_gap(app):
    for d in (6, 5):
        good_day(app, 31, d)
    app.ach_evaluate_member(31)
    good_day(app, 31, 4)
    app.ach_evaluate_member(31)
    assert app.ach_load_streak_row(31)["current_streak"] == 3
    # the member is then silent for days: the saved run is over (shown as 0) but the record stays
    data = app.ach_member_data(31)
    assert data["streak"]["current"] == 0 and data["streak"]["longest"] == 3
    good_day(app, 31, 1)                      # back yesterday after a gap -> a NEW run starting at 1
    app.ach_evaluate_member(31)
    row = app.ach_load_streak_row(31)
    assert (row["current_streak"], row["longest_streak"]) == (1, 3)
    assert app.ach_member_data(31)["streak"]["current"] == 1


def test_late_arriving_older_session_does_not_inflate_streak(app):
    good_day(app, 32, 5)
    good_day(app, 32, 4)
    good_day(app, 32, 2)
    good_day(app, 32, 1)                      # gap at 3 days ago
    app.ach_evaluate_member(32)
    row = app.ach_load_streak_row(32)
    assert (row["current_streak"], row["longest_streak"]) == (2, 2)
    good_day(app, 32, 3)                      # the missing day turns up late
    app.ach_evaluate_member(32)
    row = app.ach_load_streak_row(32)
    assert (row["current_streak"], row["longest_streak"]) == (2, 2)


def test_streak_never_goes_backwards_in_the_database(app):
    for d in range(1, 5):
        good_day(app, 33, d)
    app.ach_evaluate_member(33)
    with app._cursor() as cur:
        cur.execute("DELETE FROM vc_sessions WHERE user_id='33'")        # history vanishes
    app.ach_evaluate_member(33)
    row = app.ach_load_streak_row(33)
    assert row["longest_streak"] == 4 and row["current_streak"] == 4


def test_unsafe_timezone_pauses_calendar_progress(app, monkeypatch):
    for d in range(1, 8):
        good_day(app, 34, d)
    monkeypatch.setattr(app, "TIMEZONE_CONFIGURED", False)
    app.ach_evaluate_member(34)
    assert unlocked(app, 34) == {"first_connection"}
    assert app.ach_load_streak_row(34)["longest_streak"] == 0
    monkeypatch.undo()
    app.ach_evaluate_member(34)
    assert "seven_day_run" in unlocked(app, 34)


# ------------------------------------------------------------ backfill / reconcile / scan
def test_reconcile_pays_once_and_is_repeatable(app):
    for u in (40, 41, 42):
        for d in range(1, 8):
            good_day(app, u, d)
    r = app.ach_reconcile_batch(limit=10)
    assert r["done"] and r["processed"] == 3 and r["errors"] == 0 and r["unlocked"] == 6
    paid = [balance(app, u) for u in (40, 41, 42)]
    assert paid == [40, 40, 40] and r["points"] == 120
    r2 = app.ach_reconcile_batch(limit=10)
    assert r2["unlocked"] == 0 and r2["points"] == 0
    assert [balance(app, u) for u in (40, 41, 42)] == paid
    assert count(app, "achievement_rewards") == 6


def test_reconcile_is_resumable_in_small_batches(app):
    for u in (50, 51, 52):
        good_day(app, u, 1)
    first = app.ach_reconcile_batch(limit=2)
    assert first["processed"] == 2 and first["done"] is False
    second = app.ach_reconcile_batch(limit=2)
    assert second["processed"] == 1 and second["done"] is True
    assert {u for u in (50, 51, 52) if unlocked(app, u)} == {50, 51, 52}
    assert app.ach_get_settings()["backfill_cursor"] == ""
    again = app.ach_reconcile_batch(limit=2)           # next lap starts over harmlessly
    assert again["processed"] == 2 and again["unlocked"] == 0
    assert app.ach_reconcile_batch(limit=2, restart=True)["processed"] == 2


def test_reconcile_never_marks_anything_for_notification(app):
    good_day(app, 53, 1)
    assert app.ach_evaluate_member(53, silent=True)["notify"] == []


def test_reconcile_survives_one_bad_member_and_reports_it(app, monkeypatch):
    for u in (54, 55):
        good_day(app, u, 1)
    real = app.ach_evaluate_member

    def flaky(uid, **kw):
        if str(uid) == "54":
            raise RuntimeError("bad data")
        return real(uid, **kw)

    monkeypatch.setattr(app, "ach_evaluate_member", flaky)
    r = app.ach_reconcile_batch(limit=10)
    assert r["errors"] == 1 and r["processed"] == 2 and unlocked(app, 55) == {"first_connection"}


def test_historical_members_first_evaluation_is_silent_but_new_ones_are_not(app):
    old = now_utc() - timedelta(days=30)
    add_session(app, 56, old, old + timedelta(hours=1))                       # active BEFORE launch
    app.ach_set_setting("launch_at", (now_utc() - timedelta(days=1)).isoformat())
    r = app.ach_evaluate_member(56)
    assert r["new"] and r["notify"] == [] and r["historical"] is True
    fresh_start = now_utc() - timedelta(hours=3)
    add_session(app, 57, fresh_start, fresh_start + timedelta(hours=1))        # started AFTER launch
    r = app.ach_evaluate_member(57)
    assert [n["id"] for n in r["notify"]] == ["first_connection"]


def test_scan_first_tick_only_records_time_then_evaluates_closed_sessions(app):
    good_day(app, 58, 1)
    first = app.ach_scan_once()
    assert first["processed"] == 0 and unlocked(app, 58) == set()
    assert app.ach_get_settings()["last_scan"]
    app.ach_set_setting("last_scan", (now_utc() - timedelta(days=3)).isoformat())
    second = app.ach_scan_once()
    assert second["processed"] == 1 and unlocked(app, 58) == {"first_connection"}
    assert app.ach_scan_once()["processed"] == 0                               # nothing new since


def test_scan_respects_its_batch_limit_and_continues_next_tick(app):
    app.ach_scan_once()
    app.ach_set_setting("last_scan", (now_utc() - timedelta(days=5)).isoformat())
    for u in range(60, 65):
        good_day(app, u, 1)
    a = app.ach_scan_once(limit=2)
    b = app.ach_scan_once(limit=2)
    c = app.ach_scan_once(limit=2)
    assert (a["processed"], b["processed"]) == (2, 2) and c["processed"] >= 1
    assert all(unlocked(app, u) for u in range(60, 65))


def test_on_demand_refresh_is_throttled(app):
    good_day(app, 66, 1)
    assert "skipped" not in app.ach_refresh_if_stale(66)
    good_day(app, 66, 2)
    assert app.ach_refresh_if_stale(66)["skipped"] is True
    assert app.ach_refresh_if_stale(66, max_age=0).get("skipped") is None


# ------------------------------------------------------------ member data, profile, leaderboard
def test_member_with_no_data_gets_a_clean_empty_result(app):
    d = app.ach_member_data(999)
    assert d["evaluated"] is False and d["unlocked"] == [] and d["streak"] == {"current": 0, "longest": 0, "last_date": None}
    assert len(d["progress"]) == len(d["catalogue"]) and all(p["value"] == 0 for p in d["progress"])


def test_member_data_reports_saved_progress_for_locked_achievements(app):
    for d in range(1, 4):
        good_day(app, 70, d)
    app.ach_evaluate_member(70)
    d = app.ach_member_data(70)
    prog = {p["id"]: p for p in d["progress"]}
    assert prog["regular"]["value"] == 3 and prog["seven_day_run"]["value"] == 3
    assert d["unlocked_ids"] == {"first_connection"} and d["streak"]["current"] == 3


def test_leaderboard_ranks_by_achievements_not_wallet(app):
    app.add_balance(80, 1_000_000)                 # rich, but nothing earned
    app.add_balance(81, 5)
    for d in range(1, 8):
        good_day(app, 81, d)
    good_day(app, 82, 1)
    app.ach_evaluate_member(81)
    app.ach_evaluate_member(82)
    lb = app.ach_leaderboard()
    assert [r["user_id"] for r in lb["rows"]] == ["81", "82"]
    assert lb["rows"][0]["unlocked"] == 2 and lb["rows"][0]["points"] == 40      # ledger points, not wallet
    assert lb["rows"][1]["points"] == 10 and lb["total_members"] == 2


def test_leaderboard_pagination_bounds_and_cache(app):
    for u in range(90, 96):
        good_day(app, u, 1)
        app.ach_evaluate_member(u)
    p1 = app.ach_leaderboard(1, per=4)
    p2 = app.ach_leaderboard(2, per=4)
    assert len(p1["rows"]) == 4 and len(p2["rows"]) == 2 and p1["pages"] == 2
    assert app.ach_leaderboard(-5)["page"] == 1
    assert app.ach_leaderboard(10 ** 6)["page"] == app.ACH_LEADERBOARD_MAX_PAGE
    assert app.ach_leaderboard(1, per=4) is p1                                  # served from the short cache


def test_leaderboard_empty(app):
    lb = app.ach_leaderboard()
    assert lb["rows"] == [] and lb["total_members"] == 0 and lb["pages"] == 1


# ------------------------------------------------------------ settings
def test_settings_defaults_and_clamping(app):
    s = app.ach_get_settings()
    assert s["enabled"] and s["rewards_enabled"] and not s["notify_dm"]
    assert s["daily_minutes"] == 15 and s["announce_channel_id"] is None and s["overrides"] == {}
    app.ach_set_setting("daily_minutes", 9999)
    assert app.ach_get_settings()["daily_minutes"] == 120
    app.ach_set_setting("announce_channel_id", "12345")
    assert app.ach_get_settings()["announce_channel_id"] == 12345
    with pytest.raises(ValueError):
        app.ach_set_setting("password", "x")


def test_threshold_setting_changes_what_counts_as_a_qualifying_day(app):
    base = (now_utc() - timedelta(days=1)).replace(hour=5, minute=0, second=0, microsecond=0)
    add_session(app, 97, base, base + timedelta(minutes=10))
    app.ach_evaluate_member(97)
    assert app.ach_load_streak_row(97)["longest_streak"] == 0
    app.ach_set_setting("daily_minutes", 5)
    app.ach_evaluate_member(97)
    assert app.ach_load_streak_row(97)["longest_streak"] == 1


def test_corrupt_stored_settings_fall_back_safely(app):
    with app._cursor() as cur:
        cur.execute("INSERT INTO bot_settings (key,value) VALUES ('achievements_overrides','{not json') "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
        cur.execute("INSERT INTO bot_settings (key,value) VALUES ('achievements_daily_minutes','banana') "
                    "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value")
    s = app.ach_get_settings()
    assert s["overrides"] == {} and s["daily_minutes"] == 15
    assert len(app.ach_catalogue(s)) == 8
