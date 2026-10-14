"""
Integration tests for the reservation lifecycle against a REAL PostgreSQL.
Skipped unless TEST_DATABASE_URL is set (e.g. postgresql://postgres:pw@localhost/vct).
Discord objects are replaced by tiny fakes; nothing connects to Discord.
"""
import asyncio, importlib.util, os, sys
from datetime import datetime, timedelta, timezone

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = lambda: datetime.now(timezone.utc)


def _load_app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_under_test", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app():
    mod = _load_app()
    assert mod.init_db()
    return mod


import discord


class FakeUser:
    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append(text)


class FakeBot:
    def __init__(self):
        self.user_obj = FakeUser()

    def get_user(self, _):
        return self.user_obj

    def get_channel(self, _):
        return None

    def get_guild(self, _):
        return getattr(self, "guild", None)

    def is_closed(self):
        return False


class FakeMember:
    def __init__(self, uid, bot=False):
        self.id, self.bot = uid, bot
        self.mention = f"<@{uid}>"
        self.moved = False

    async def move_to(self, channel, reason=None):
        self.moved = True


class FakeVC(discord.VoiceChannel):
    def __init__(self, cid):
        self.id = cid
        self._m = []
        self.edits = []

    @property
    def members(self):
        return self._m

    async def edit(self, **kw):
        self.edits.append(kw)


class FakeGuild:
    def __init__(self, channels):
        self.default_role = "everyone"
        self.me = "bot"
        self._ch = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self._ch.get(cid)

    def get_member(self, uid):
        return FakeMember(uid)


@pytest.fixture()
def env(app):
    with app._cursor() as cur:
        cur.execute("TRUNCATE reservation_members, temp_vc_pool, reservations RESTART IDENTITY CASCADE")
    fb = FakeBot()
    app.bot = fb
    pool = FakeVC(9001)
    guild = FakeGuild([pool, FakeVC(555)])
    fb.guild = guild
    app.register_temp_vc_pool_channel(9001, "Temp VC 1")
    return fb, guild, pool


def mk(app, rtype="existing_vc", channel=555, start=None, end=None, name="Game", lock="locked", user="10"):
    start = start or NOW() + timedelta(hours=1)
    end = end or start + timedelta(hours=2)
    with app._cursor() as cur:
        cur.execute("""INSERT INTO reservations (guild_id, requester_id, requester_name, reservation_name, reservation_type,
            requested_channel_id, requested_channel_name, start_time, end_time, status, lock_state)
            VALUES ('1', %s, 'U', %s, %s, %s, 'room', %s, %s, 'pending', %s) RETURNING id""",
                    (user, name, rtype, str(channel) if rtype == "existing_vc" else None, start, end, lock))
        rid = cur.fetchone()["id"]
        cur.execute("INSERT INTO reservation_members VALUES (%s, %s, 'U')", (rid, user))
    return rid


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_double_approve_and_conflict(app, env):
    a, b = mk(app), mk(app, user="11")
    assert app.approve_reservation(a, "adm", "existing")[0] == "ok"
    assert app.approve_reservation(a, "adm", "existing")[0] == "not_pending"
    res, info = app.approve_reservation(b, "adm", "existing")
    assert res == "conflict" and info["id"] == a
    assert app.get_reservation(b)["status"] == "pending"


def test_back_to_back_not_conflict(app, env):
    s = NOW() + timedelta(hours=1)
    a = mk(app, start=s, end=s + timedelta(hours=1))
    b = mk(app, start=s + timedelta(hours=1), end=s + timedelta(hours=2), user="11")
    assert app.approve_reservation(a, "x", "existing")[0] == "ok"
    assert app.approve_reservation(b, "x", "existing")[0] == "ok"


def test_ended_pending_cannot_be_approved_and_expires(app, env):
    r = mk(app, start=NOW() - timedelta(hours=3), end=NOW() - timedelta(hours=1))
    assert app.approve_reservation(r, "x", "existing")[0] == "ended"
    assert app.expire_stale_pending_reservations() == [r]
    assert app.get_reservation(r)["status"] == "expired"


def test_private_picks_by_window(app, env):
    app.register_temp_vc_pool_channel(9002, "Temp VC 2")
    s = NOW() + timedelta(hours=2)
    a = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    b = mk(app, "private_vc", start=s, end=s + timedelta(hours=1), user="11")
    c = mk(app, "private_vc", start=s, end=s + timedelta(hours=1), user="12")
    assert app.approve_reservation(a, "x", "private")[1]["channel_id"] == "9001"
    assert app.approve_reservation(b, "x", "private")[1]["channel_id"] == "9002"
    assert app.approve_reservation(c, "x", "private")[0] == "no_channel"
    # approval alone must not flip the pool or touch Discord
    assert app.get_pool_row(9001)["status"] == "available"
    # a later, non-overlapping slot can reuse channel 1
    d = mk(app, "private_vc", start=s + timedelta(hours=3), end=s + timedelta(hours=4), user="13")
    assert app.approve_reservation(d, "x", "private")[1]["channel_id"] == "9001"


def test_request_rejects_pool_channel_and_overlap(app, env):
    s = NOW() + timedelta(hours=5)
    ok, msg = app.create_reservation_request(1, 20, "A", "n", "existing_vc", 9001, "pool", s, s + timedelta(hours=1), None)
    assert not ok and "private VCs" in msg
    r = mk(app, start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "existing")
    ok, msg = app.create_reservation_request(1, 21, "B", "n", "existing_vc", 555, "room", s + timedelta(minutes=30), s + timedelta(hours=2), None)
    assert not ok and "already reserved" in msg


def test_window_enforcement_is_time_aware(app, env):
    s = NOW() + timedelta(hours=1)
    r = mk(app, start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "existing")
    f = lambda at: run(app.find_active_reservation_for_channel(555, at))
    assert f(s - timedelta(minutes=1)) is None
    assert f(s)["id"] == r
    assert f(s + timedelta(minutes=59))["id"] == r
    assert f(s + timedelta(hours=1)) is None


def test_tick_ends_reservations(app, env):
    fb, guild, pool = env
    past = NOW() - timedelta(minutes=5)
    a = mk(app, start=past - timedelta(hours=1), end=past)
    b = mk(app, start=past - timedelta(hours=1), end=past, user="11", channel=556)
    for r in (a, b):
        with app._cursor() as cur:
            cur.execute("UPDATE reservations SET status='approved', assigned_channel_id=requested_channel_id WHERE id=%s", (r,))
    app.update_reservation_status(a, "active")
    run(app.reservation_tick())
    assert app.get_reservation(a)["status"] == "completed"
    assert app.get_reservation(b)["status"] == "expired"
    run(app.reservation_tick())  # idempotent
    assert app.get_reservation(a)["status"] == "completed"


def test_private_lifecycle(app, env):
    fb, guild, pool = env
    s = NOW() + timedelta(seconds=30)  # inside the 1-minute lead
    r = mk(app, "private_vc", start=s, end=s + timedelta(hours=1), lock="locked")
    assert app.approve_reservation(r, "x", "private")[0] == "ok"
    run(app.reservation_tick())
    assert len(pool.edits) == 1 and app.get_pool_row(9001)["status"] == "in_use"
    assert pool.edits[0]["overwrites"]["everyone"].connect is False
    run(app.reservation_tick())
    assert len(pool.edits) == 1  # idempotent
    # unlock mid-window -> everyone may connect
    app.set_reservation_lock_state(r, "unlocked")
    assert run(app.provision_temp_vc_for_reservation(guild, r, force=True))
    assert pool.edits[-1]["overwrites"]["everyone"].connect is True
    # emptying mid-window must NOT recycle
    run(app.maybe_recycle_pool_on_empty(guild, pool))
    assert app.get_pool_row(9001)["status"] == "in_use"
    # end of window: expire -> reconcile resets the channel
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET start_time = NOW() - interval '2 hours', end_time = NOW() - interval '1 minute' WHERE id=%s", (r,))
    run(app.reservation_tick())
    assert app.get_reservation(r)["status"] == "expired"
    assert app.get_pool_row(9001)["status"] == "available"
    assert pool.edits[-1]["name"] == "Temp VC 1"


def test_occupied_pool_waits_for_grace_then_disconnects(app, env):
    fb, guild, pool = env
    s = NOW() + timedelta(seconds=30)
    r = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "private")
    run(app.reservation_tick())
    m = FakeMember(1)
    pool._m.append(m)
    app.update_reservation_status(r, "active")
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET start_time = NOW() - interval '2 hours', end_time = NOW() - interval '1 minute' WHERE id=%s", (r,))
    run(app.reservation_tick())
    assert app.get_reservation(r)["status"] == "completed"
    assert app.get_pool_row(9001)["status"] == "in_use" and not m.moved  # within grace, occupied
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET end_time = NOW() - interval '20 minutes' WHERE id=%s", (r,))
    run(app.reservation_tick())
    assert m.moved and app.get_pool_row(9001)["status"] == "available"


def test_cancel_cleanup(app, env):
    fb, guild, pool = env
    s = NOW() + timedelta(seconds=30)
    r = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "private")
    run(app.reservation_tick())
    assert run(app.cancel_reservation_and_cleanup(guild, r))
    assert app.get_reservation(r)["status"] == "cancelled"
    assert app.get_pool_row(9001)["status"] == "available"
    assert not run(app.cancel_reservation_and_cleanup(guild, r))  # repeat is a no-op
    # existing VC: cancel lifts restriction immediately
    e = mk(app)
    app.approve_reservation(e, "x", "existing")
    assert run(app.cancel_reservation_and_cleanup(guild, e))
    assert run(app.find_active_reservation_for_channel(555, NOW() + timedelta(hours=2))) is None


def test_session_tagging_respects_window(app, env):
    s = NOW() + timedelta(hours=1)
    r = mk(app, start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "existing")
    app.open_or_move_session("77", "Z", 555, "room")  # now: before the window
    with app._cursor() as cur:
        cur.execute("SELECT reservation_id FROM vc_sessions WHERE user_id='77' AND ended_at IS NULL")
        assert cur.fetchone()["reservation_id"] is None
        cur.execute("DELETE FROM vc_sessions WHERE user_id='77'")


def test_legacy_sync_idempotent(app, env):
    with app._cursor() as cur:
        cur.execute("DELETE FROM vc_reservations")
        cur.execute("""INSERT INTO vc_reservations (user_id, user_name, channel_id, channel_name, start_time, end_time, duration_minutes)
                       VALUES ('5','L','555','room', NOW() + interval '1 day', NOW() + interval '1 day 1 hour', 60)""")
    app.sync_unmigrated_legacy_reservations()
    app.sync_unmigrated_legacy_reservations()
    with app._cursor() as cur:
        cur.execute("SELECT status, lock_state FROM reservations WHERE requester_id='5'")
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "approved" and rows[0]["lock_state"] == "unlocked"
    with app._cursor() as cur:
        cur.execute("DELETE FROM vc_reservations")


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, text=None, **kw):
        self.messages.append(text)

    async def edit_message(self, **kw):
        self.messages.append("edited")


class FakeInteraction:
    def __init__(self, user, guild=None):
        self.user, self.guild, self.response = user, guild, FakeResponse()


def _fake_member(app, admin):
    class M(discord.Member):
        def __init__(self):
            pass
        guild_permissions = discord.Permissions(administrator=admin)
        display_name = "adm"
    return M()


def test_non_admin_cannot_approve_or_decline(app, env):
    r = mk(app)
    for handler in (lambda i: app._handle_approve(i, r, "existing"), lambda i: app._handle_decline(i, r)):
        i = FakeInteraction(_fake_member(app, admin=False))
        run(handler(i))
        assert "administrators" in i.response.messages[0]
        assert app.get_reservation(r)["status"] == "pending"
    # a plain user object (e.g. DM, no guild permissions) is also refused
    i = FakeInteraction(FakeMember(5))
    run(app._handle_decline(i, r))
    assert app.get_reservation(r)["status"] == "pending"


def test_admin_can_decline(app, env):
    r = mk(app)
    i = FakeInteraction(_fake_member(app, admin=True), guild=env[1])
    i.message = None
    run(app._handle_decline(i, r))
    assert app.get_reservation(r)["status"] == "declined"
