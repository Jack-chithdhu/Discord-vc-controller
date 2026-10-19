"""
Discord-command, notification and website tests for Member Achievements & Streaks.
Drives the REAL slash-command callbacks and Flask routes against a REAL PostgreSQL;
skipped unless TEST_DATABASE_URL is set. Only Discord objects are fakes.
"""
import asyncio, importlib.util, os, sys
from datetime import datetime, timedelta, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = timezone.utc

import discord
from discord import app_commands


@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_ach_discord_test", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    mod.bot = mod.make_bot()
    return mod


@pytest.fixture(autouse=True)
def clean(app):
    with app._cursor() as cur:
        for sql in ("DELETE FROM achievement_rewards", "DELETE FROM member_achievements",
                    "DELETE FROM member_streaks", "DELETE FROM vc_sessions", "DELETE FROM points",
                    "DELETE FROM bot_settings WHERE key LIKE 'achievements\\_%'"):
            cur.execute(sql)
    app._ach_leaderboard_cache.clear()
    real_bot = app.bot
    yield
    app.bot = real_bot


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------- fakes
class Resp:
    def __init__(self):
        self.sent, self.deferred = [], None

    async def defer(self, **kw):
        self.deferred = kw

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))


class Inter:
    def __init__(self, user):
        self.user, self.response, self.guild = user, Resp(), None
        outer = self

        class _F:
            async def send(self, content=None, **kw):
                outer.response.sent.append((content, kw))
        self.followup = _F()

    @property
    def last(self):
        return self.response.sent[-1]

    @property
    def embed(self):
        return self.last[1]["embed"]


def mk_member(uid, admin=False, name="Tester"):
    class M(discord.Member):
        id = uid
        display_name = name
        guild_permissions = discord.Permissions(administrator=admin)

        def __init__(self):
            pass
    return M()


def cmd(app, name):
    return app.bot.tree.get_command(name).callback


def choice(value):
    return app_commands.Choice(name=str(value), value=value)


def good_day(app, user, days_ago, hours=1.0, channel="100"):
    local_today = (datetime.now(UTC) + timedelta(hours=5.5)).date()
    d = local_today - timedelta(days=days_ago)
    base = datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(hours=5.5) + timedelta(hours=10)
    with app._cursor() as cur:
        cur.execute(
            "INSERT INTO vc_sessions (user_id, user_name, channel_id, channel_name, started_at, ended_at, duration_seconds) "
            "VALUES (%s,'Member',%s,'General',%s,%s,%s)",
            (str(user), channel, base, base + timedelta(hours=hours), hours * 3600))


def embed_text(e):
    return "\n".join([e.title or "", e.description or ""] + [f"{f.name}\n{f.value}" for f in e.fields] +
                     [e.footer.text if e.footer and e.footer.text else ""])


def assert_embed_ok(e):
    assert len(e) <= 6000 and len(e.title or "") <= 256 and len(e.description or "") <= 4096
    assert len(e.fields) <= 25
    for f in e.fields:
        assert 0 < len(f.name) <= 256 and 0 < len(f.value) <= 1024


# ---------------------------------------------------------------- registration / permissions
MEMBER_CMDS = ("achievements", "streak", "achievement-leaderboard")
ADMIN_CMDS = ("achievement-settings", "achievement-inspect", "achievement-reconcile")


def test_commands_registered_with_right_permissions(app):
    for name in MEMBER_CMDS:
        c = app.bot.tree.get_command(name)
        assert c is not None and c.default_permissions is None
    for name in ADMIN_CMDS:
        assert app.bot.tree.get_command(name).default_permissions.administrator
    for name in ("daily", "balance", "points-leaderboard", "who-was-with-me", "my-vc-report", "my-profile"):
        assert app.bot.tree.get_command(name) is not None            # nothing existing was displaced


def test_admin_commands_reject_normal_members_and_change_nothing(app):
    good_day(app, 5, 1)
    i = Inter(mk_member(5))
    run(cmd(app, "achievement-settings")(i, setting=choice("feature"), value="off"))
    assert "administrators" in i.last[0]
    assert app.ach_get_settings()["enabled"] is True
    i = Inter(mk_member(5))
    run(cmd(app, "achievement-inspect")(i, mk_member(6)))
    assert "administrators" in i.last[0] and i.response.deferred is None
    i = Inter(mk_member(5))
    run(cmd(app, "achievement-reconcile")(i))
    assert "administrators" in i.last[0]
    with app._cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM member_achievements")
        assert cur.fetchone()["n"] == 0


# ---------------------------------------------------------------- /achievements
def test_achievements_for_a_member_with_no_history(app):
    i = Inter(mk_member(10, name="Newbie"))
    run(cmd(app, "achievements")(i))
    assert i.response.deferred == {"ephemeral": True} and i.last[1]["ephemeral"] is True
    e = i.embed
    assert_embed_ok(e)
    assert "Unlocked (0/8)" in e.description and "Nothing yet" in e.description
    assert "Newbie" in e.title
    assert "Not started" in e.description and "First Connection" in e.description


def test_achievements_unlocks_and_shows_progress_and_rewards_only_when_relevant(app):
    for d in range(1, 4):
        good_day(app, 11, d)
    i = Inter(mk_member(11, name="Pat"))
    run(cmd(app, "achievements")(i))
    e = i.embed
    assert_embed_ok(e)
    text = e.description
    assert "Unlocked (1/8)" in text and "First Connection" in text
    assert "In progress" in text and "Regular" in text and "3/10 sessions" in text
    assert "+25 pts" in text                                   # reward shown for a LOCKED achievement
    unlocked_part = text.split("In progress")[0]
    assert "pts" not in unlocked_part                          # not for an unlocked one
    assert app.get_balance(11) == 10


def test_achievements_hide_rewards_when_rewards_are_off(app):
    app.ach_set_setting("rewards_enabled", "off")
    good_day(app, 12, 1)
    i = Inter(mk_member(12))
    run(cmd(app, "achievements")(i))
    assert "pts" not in i.embed.description and app.get_balance(12) == 0


def test_achievements_when_feature_is_off(app):
    app.ach_set_setting("enabled", "off")
    i = Inter(mk_member(13))
    run(cmd(app, "achievements")(i))
    assert "turned off" in i.last[0] and i.last[1]["ephemeral"] is True


def test_command_spam_does_not_pay_or_re_evaluate(app):
    good_day(app, 14, 1)
    for _ in range(6):
        run(cmd(app, "achievements")(Inter(mk_member(14))))
    assert app.get_balance(14) == 10
    with app._cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM achievement_rewards WHERE user_id='14'")
        assert cur.fetchone()["n"] == 1


def test_achievements_survives_an_evaluation_failure_and_shows_saved_data(app, monkeypatch):
    good_day(app, 15, 1)
    run(cmd(app, "achievements")(Inter(mk_member(15))))
    monkeypatch.setattr(app, "ach_refresh_if_stale", lambda uid: (_ for _ in ()).throw(RuntimeError("db hiccup")))
    i = Inter(mk_member(15))
    run(cmd(app, "achievements")(i))
    assert "First Connection" in i.embed.description              # saved data still shown


def test_achievements_only_ever_shows_the_callers_own_data(app):
    good_day(app, 16, 1)
    run(cmd(app, "achievements")(Inter(mk_member(16))))
    other = Inter(mk_member(17))
    run(cmd(app, "achievements")(other))
    assert "Unlocked (0/8)" in other.embed.description


# ---------------------------------------------------------------- /streak
def test_streak_embed_contents(app):
    for d in (3, 2, 1):
        good_day(app, 20, d)
    i = Inter(mk_member(20, name="Sam"))
    run(cmd(app, "streak")(i))
    e = i.embed
    assert_embed_ok(e) and i.last[1]["ephemeral"] is True
    fields = {f.name: f.value for f in e.fields}
    assert fields["Current streak"] == "**3** days" and fields["Longest streak"] == "**3** days"
    assert fields["Last qualifying day"] != "—"
    goal = next(v for k, v in fields.items() if k.startswith("Next goal"))
    assert "3/7 days" in goal and "+30 pts" in goal
    rule = fields["How a day counts"]
    assert "15 minutes" in rule and "UTC+5.5" in rule and "estimate" in rule


def test_streak_for_new_member_and_when_all_streak_goals_done(app):
    i = Inter(mk_member(21))
    run(cmd(app, "streak")(i))
    f = {x.name: x.value for x in i.embed.fields}
    assert f["Current streak"] == "**0** days" and f["Last qualifying day"] == "—"
    assert "No qualifying day yet" in i.embed.footer.text and "15+ minutes" in i.embed.footer.text
    for d in range(1, 8):
        good_day(app, 22, d)
    run(cmd(app, "streak")(Inter(mk_member(22))))
    i = Inter(mk_member(22))
    run(cmd(app, "streak")(i))
    assert any(f.name == "Streak achievements" for f in i.embed.fields)


def test_streak_respects_the_configured_minutes(app):
    app.ach_set_setting("daily_minutes", 30)
    i = Inter(mk_member(23))
    run(cmd(app, "streak")(i))
    assert "30 minutes" in {f.name: f.value for f in i.embed.fields}["How a day counts"]


# ---------------------------------------------------------------- /achievement-leaderboard
def test_leaderboard_is_public_ranked_and_explains_points(app):
    app.add_balance(30, 999999)
    for d in range(1, 8):
        good_day(app, 31, d)
    good_day(app, 32, 1)
    for u in (31, 32):
        app.ach_evaluate_member(u)
    i = Inter(mk_member(1))
    run(cmd(app, "achievement-leaderboard")(i))
    assert i.response.deferred == {} and "ephemeral" not in i.last[1]
    e = i.embed
    assert_embed_ok(e)
    lines = e.description.split("\n")
    assert lines[0].startswith("**1.** <@31>") and "2/8" in lines[0] and "40 pts" in lines[0]
    assert lines[1].startswith("**2.** <@32>") and "10 pts" in lines[1]
    assert "<@30>" not in e.description                              # wallet is irrelevant
    assert "not wallet" in e.footer.text and "Page 1/1" in e.footer.text


def test_leaderboard_empty_and_disabled_and_paging(app):
    i = Inter(mk_member(1))
    run(cmd(app, "achievement-leaderboard")(i))
    assert "No one has unlocked" in i.embed.description
    for u in range(40, 52):
        good_day(app, u, 1)
        app.ach_evaluate_member(u)
    i = Inter(mk_member(1))
    run(cmd(app, "achievement-leaderboard")(i, page=2))
    assert i.embed.description.startswith("**11.**") and "Page 2/2" in i.embed.footer.text
    app.ach_set_setting("enabled", "off")
    i = Inter(mk_member(1))
    run(cmd(app, "achievement-leaderboard")(i))
    assert "turned off" in i.last[0]


def test_leaderboard_page_option_is_bounded(app):
    opt = next(p for p in app.bot.tree.get_command("achievement-leaderboard").parameters if p.name == "page")
    assert (opt.min_value, opt.max_value) == (1, 50)


# ---------------------------------------------------------------- admin settings
def run_setting(app, **kw):
    i = Inter(mk_member(1, admin=True))
    for k in ("setting", "achievement"):
        if kw.get(k) is not None:
            kw[k] = choice(kw[k])
    run(cmd(app, "achievement-settings")(i, **kw))
    return i


def test_settings_view_lists_everything(app):
    i = run_setting(app)
    e = i.embed
    assert_embed_ok(e) and i.last[1]["ephemeral"] is True
    text = embed_text(e)
    assert "Feature: **ON**" in text and "Unlock DMs: **OFF**" in text and "15 min" in text
    assert "`first_connection`" in text and "`dedicated`" in text and "+100 pts" in text


@pytest.mark.parametrize("setting,key,value,expect", [
    ("feature", "enabled", "off", False), ("rewards", "rewards_enabled", "off", False),
    ("dm", "notify_dm", "on", True), ("feature", "enabled", "on", True)])
def test_toggle_settings(app, setting, key, value, expect):
    i = run_setting(app, setting=setting, value=value)
    assert "✅" in i.last[0] and app.ach_get_settings()[key] is expect


def test_numeric_and_override_settings(app):
    assert "✅" in run_setting(app, setting="daily_minutes", value="25").last[0]
    assert app.ach_get_settings()["daily_minutes"] == 25
    assert "✅" in run_setting(app, setting="reward", value="5", achievement="regular").last[0]
    assert "✅" in run_setting(app, setting="target", value="3", achievement="regular").last[0]
    assert "✅" in run_setting(app, setting="achievement", value="off", achievement="dedicated").last[0]
    by = app.al.catalogue_by_id(app.ach_catalogue())
    assert (by["regular"].reward, by["regular"].target, by["dedicated"].enabled) == (5, 3, False)


@pytest.mark.parametrize("kw", [
    dict(setting="feature", value="maybe"), dict(setting="daily_minutes", value="3"),
    dict(setting="daily_minutes", value="500"), dict(setting="daily_minutes", value="abc"),
    dict(setting="reward", value="5"),                                  # no achievement chosen
    dict(setting="reward", value="-1", achievement="regular"), dict(setting="reward", value="99999999", achievement="regular"),
    dict(setting="target", value="0", achievement="regular"), dict(setting="target", value="x", achievement="regular"),
    dict(setting="achievement", value="perhaps", achievement="regular"), dict(setting="announce")])
def test_invalid_settings_are_refused_and_nothing_changes(app, kw):
    before = app.ach_get_settings()
    i = run_setting(app, **kw)
    assert "⚠" in i.last[0]
    assert app.ach_get_settings() == before


def test_announce_channel_setting(app):
    class Ch:
        id = 555
    i = run_setting(app, setting="announce", channel=Ch())
    assert "<#555>" in i.last[0] and app.ach_get_settings()["announce_channel_id"] == 555
    run_setting(app, setting="announce", value="off")
    assert app.ach_get_settings()["announce_channel_id"] is None


def test_admin_cannot_use_settings_to_grant_rewards_or_progress(app):
    names = {p.name for p in app.bot.tree.get_command("achievement-settings").parameters}
    assert names == {"setting", "value", "achievement", "channel"}
    values = {c.value for c in next(p for p in app.bot.tree.get_command("achievement-settings").parameters
                                    if p.name == "setting").choices}
    assert values == {"feature", "rewards", "dm", "announce", "daily_minutes", "reward", "target", "achievement"}


# ---------------------------------------------------------------- inspect / reconcile
def test_inspect_is_read_only_and_shows_saved_state(app):
    for d in range(1, 4):
        good_day(app, 60, d)
    app.ach_evaluate_member(60)
    with app._cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM member_streaks")
        before = cur.fetchone()
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-inspect")(i, mk_member(60, name="Target")))
    e = i.embed
    assert_embed_ok(e) and i.last[1]["ephemeral"] is True
    text = embed_text(e)
    assert "Target" in e.title and "First Connection" in text and "Current 3" in text and "10" in text
    with app._cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM member_streaks")
        assert cur.fetchone() == before
    assert app.get_balance(60) == 10


def test_inspect_member_with_nothing(app):
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-inspect")(i, mk_member(61)))
    assert "Evaluated before: no" in embed_text(i.embed) and "None" in embed_text(i.embed)


def test_reconcile_one_member_and_batches(app):
    for u in (70, 71):
        good_day(app, u, 1)
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-reconcile")(i, member=mk_member(70, name="Seventy")))
    assert "First Connection" in i.last[0] and app.get_balance(70) == 10
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-reconcile")(i))
    assert "Processed 2 member(s)" in i.last[0] and "Finished a full pass" in i.last[0]
    assert app.get_balance(70) == 10 and app.get_balance(71) == 10          # paid once each
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-reconcile")(i))
    assert "0 new unlock" in i.last[0] and "0 points" in i.last[0]


def test_reconcile_when_disabled_and_for_a_bot(app):
    app.ach_set_setting("enabled", "off")
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-reconcile")(i))
    assert "turned off" in i.last[0]
    app.ach_set_setting("enabled", "on")

    class G:
        def get_member(self, _):
            return type("B", (), {"bot": True})()

    class B:
        def get_guild(self, _): return G()

    callback = cmd(app, "achievement-reconcile")
    app.bot = B()
    i = Inter(mk_member(1, admin=True))
    run(callback(i, member=mk_member(80)))
    assert "bot" in i.last[0]


def test_reconcile_reports_errors_without_crashing(app, monkeypatch):
    monkeypatch.setattr(app, "ach_reconcile_batch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    i = Inter(mk_member(1, admin=True))
    run(cmd(app, "achievement-reconcile")(i))
    assert "stopped safely" in i.last[0]


# ---------------------------------------------------------------- notifications
class FakeUser:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send(self, **kw):
        if self.fail:
            raise self.fail
        self.sent.append(kw)


class FakeChannel:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send(self, text, **kw):
        if self.fail:
            raise self.fail
        self.sent.append((text, kw))


class FakeBotN:
    def __init__(self, user=None, channel=None):
        self.user, self.channel = user, channel

    def get_user(self, _):
        return self.user

    async def fetch_user(self, _):
        return self.user

    def get_channel(self, _):
        return self.channel

    def get_guild(self, _):
        return None


def forbidden():
    return discord.Forbidden(type("R", (), {"status": 403, "reason": "no"})(), "no")


NEW = [{"id": "regular", "name": "Regular", "emoji": "\U0001F4C6", "paid": 25, "reward": 25}]


def test_no_notification_by_default(app):
    user = FakeUser()
    app.bot = FakeBotN(user, FakeChannel())
    run(app._ach_notify(5, NEW))
    assert user.sent == []


def test_dm_notification_when_enabled_and_dm_failure_is_swallowed(app, capsys):
    app.ach_set_setting("notify_dm", "on")
    user = FakeUser()
    app.bot = FakeBotN(user)
    run(app._ach_notify(5, NEW))
    assert len(user.sent) == 1
    emb = user.sent[0]["embed"]
    assert "Regular" in emb.description and "+25 points" in emb.description
    app.bot = FakeBotN(FakeUser(fail=forbidden()))
    run(app._ach_notify(5, NEW))                                         # must not raise
    assert "could not DM" in capsys.readouterr().out


def test_announcement_channel_does_not_ping_and_failures_are_swallowed(app):
    app.ach_set_setting("announce_channel_id", "999")
    ch = FakeChannel()
    app.bot = FakeBotN(FakeUser(), ch)
    run(app._ach_notify(5, NEW))
    text, kw = ch.sent[0]
    assert "<@5>" in text and "Regular" in text
    assert isinstance(kw["allowed_mentions"], discord.AllowedMentions) and not kw["allowed_mentions"].users
    app.bot = FakeBotN(FakeUser(), FakeChannel(fail=forbidden()))
    run(app._ach_notify(5, NEW))


def test_a_failed_notification_never_touches_rewards(app):
    app.ach_set_setting("notify_dm", "on")
    good_day(app, 90, 1)
    result = app.ach_evaluate_member(90)
    app.bot = FakeBotN(FakeUser(fail=forbidden()))
    run(app._ach_notify(90, result["notify"]))
    assert app.get_balance(90) == 10
    app.ach_evaluate_member(90)
    assert app.get_balance(90) == 10


def test_background_loop_scans_and_notifies_once(app, monkeypatch):
    app.ach_set_setting("notify_dm", "on")
    app.ach_set_setting("launch_at", (datetime.now(UTC) - timedelta(days=2)).isoformat())
    app.ach_scan_once()                                                  # first tick records "now"
    app.ach_set_setting("last_scan", (datetime.now(UTC) - timedelta(days=2)).isoformat())
    start = datetime.now(UTC) - timedelta(hours=3)
    with app._cursor() as cur:                                          # started after launch => notified
        cur.execute("INSERT INTO vc_sessions (user_id,user_name,channel_id,channel_name,started_at,ended_at,duration_seconds) "
                    "VALUES ('91','M','100','G',%s,%s,3600)", (start, start + timedelta(hours=1)))
    user = FakeUser()

    class LoopBot(FakeBotN):
        calls = 0

        async def wait_until_ready(self):
            pass

        def is_closed(self):
            LoopBot.calls += 1
            return LoopBot.calls > 1

    app.bot = LoopBot(user)

    async def no_sleep(_):
        pass

    monkeypatch.setattr(app.asyncio, "sleep", no_sleep)
    run(app.achievements_loop())
    assert len(user.sent) == 1 and app.get_balance(91) == 10
    assert app.job_health._jobs["achievements_scan"]["total_successes"] >= 1
    LoopBot.calls = 0
    run(app.achievements_loop())
    assert len(user.sent) == 1                                           # nothing new => nothing re-sent


def test_loop_survives_a_failing_scan(app, monkeypatch):
    class LoopBot(FakeBotN):
        calls = 0

        async def wait_until_ready(self):
            pass

        def is_closed(self):
            LoopBot.calls += 1
            return LoopBot.calls > 1

    app.bot = LoopBot()
    monkeypatch.setattr(app, "ach_scan_once", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    async def no_sleep(_):
        pass

    monkeypatch.setattr(app.asyncio, "sleep", no_sleep)
    run(app.achievements_loop())
    assert app.job_health._jobs["achievements_scan"]["total_failures"] >= 1


# ---------------------------------------------------------------- website
def client(app):
    return app.app.test_client()


def test_profile_achievements_endpoint_for_an_empty_profile(app):
    r = client(app).get("/api/vcdata/999999/achievements")
    d = r.get_json()
    assert r.status_code == 200 and d["enabled"] is True and d["evaluated"] is False
    assert d["badges"] == [] and d["latest"] == [] and d["total_unlocked"] == 0 and d["total_available"] == 8
    assert d["streak"] == {"current": 0, "longest": 0, "last_date": None}
    assert len(d["progress"]) == 4 and all(p["fraction"] == 0 for p in d["progress"])


def test_profile_achievements_endpoint_with_data(app):
    for d in range(1, 8):
        good_day(app, 95, d)
    app.ach_evaluate_member(95)
    d = client(app).get("/api/vcdata/95/achievements").get_json()
    assert d["total_unlocked"] == 2 and {b["id"] for b in d["badges"]} == {"first_connection", "seven_day_run"}
    assert d["streak"]["current"] == 7 and d["streak"]["longest"] == 7 and d["streak"]["last_date"]
    assert len(d["latest"]) == 2 and all(set(b) == {"id", "emoji", "name", "description", "unlocked_at"} for b in d["badges"])
    assert all(p["id"] not in {"first_connection", "seven_day_run"} for p in d["progress"])
    assert d["progress"] == sorted(d["progress"], key=lambda p: -p["fraction"])
    blob = str(d).lower()
    for secret in ("postgres", "database_url", "password", "token", "reward_amount", "progress_at_unlock"):
        assert secret not in blob


def test_profile_achievements_endpoint_when_feature_is_off(app):
    app.ach_set_setting("enabled", "off")
    assert client(app).get("/api/vcdata/95/achievements").get_json() == {"enabled": False}


def test_profile_achievements_endpoint_error_is_graceful(app, monkeypatch):
    monkeypatch.setattr(app, "ach_profile_payload", lambda uid: (_ for _ in ()).throw(RuntimeError("x")))
    r = client(app).get("/api/vcdata/1/achievements")
    assert r.status_code == 500 and r.get_json() == {"error": "could not load achievements"}


def test_existing_profile_api_fields_are_unchanged(app):
    good_day(app, 96, 1)
    r = client(app).get("/api/vcdata/96")
    d = r.get_json()
    assert r.status_code == 200
    assert set(d) == {"user_id", "display_name", "avatar_url", "sessions_count", "most_used_channel", "streak_days",
                      "most_active_day", "most_active_hour", "average_session", "seconds", "formatted"}
    assert set(d["seconds"]) == {"total", "today", "week", "month", "longest_session"}
    assert set(d["formatted"]) == {"total", "today", "week", "month", "longest_session"}
    for path in ("heatmap", "hourmap", "companions"):
        assert client(app).get(f"/api/vcdata/96/{path}").status_code == 200
    assert client(app).get("/api/profiles").status_code == 200


def test_profile_page_has_the_achievements_section_and_escapes_text(app):
    r = client(app).get("/profile/96")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'id="achievements"' in html and "loadAchievements" in html and "/achievements'" in html
    assert "escA(b.name)" in html and "escA(p.text)" in html and "escA(b.description)" in html
    assert "heatmap" in html and "Top VC Companions" in html            # existing sections untouched
    assert client(app).get("/profiles").status_code == 200


# ---------------------------------------------------------------- embed limits in the worst case
def test_all_embeds_respect_discord_limits_for_a_fully_loaded_member(app):
    for d in range(1, 8):
        good_day(app, 97, d)
    app.ach_evaluate_member(97)
    data = app.ach_member_data(97)
    for e in (app.ach_build_achievements_embed(data, "N" * 32),
              app.ach_build_streak_embed(data, "N" * 32),
              app.ach_build_inspect_embed(data, "N" * 32, 12345),
              app.ach_build_settings_embed(data["settings"]),
              app.ach_build_leaderboard_embed(app.ach_leaderboard(), 8)):
        assert_embed_ok(e)
