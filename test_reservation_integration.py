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
        self.name = f"vc-{cid}"

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

    @property
    def voice_channels(self):
        return list(self._ch.values())

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


# ---------- Issue 2: private VC allocation (root-cause regression tests) ----------

def set_status(app, rid, status, channel="9001"):
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET status=%s, assigned_channel_id=%s WHERE id=%s", (status, channel, rid))

def test_empty_pool_gives_helpful_message(app, env):
    with app._cursor() as cur:
        cur.execute("DELETE FROM temp_vc_pool")
    s = NOW() + timedelta(hours=2)
    ok, msg = app.create_reservation_request(1, 30, "A", "Gaming", "private_vc", None, None, s, s + timedelta(hours=1), None)
    assert not ok and "aren't set up" in msg and "reserved" not in msg
    r = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    res, info = app.approve_reservation(r, "x", "private")
    assert res == "no_channel" and info["pool_size"] == 0
    assert "pool is empty" in app._no_private_vc_message(info, s, s + timedelta(hours=1), "admin")


def test_reported_booking_12_52_to_12_55_ist_is_allocated_after_earlier_test_bookings(app, env):
    """The live failure: 14 Oct 2026 12:52-12:55 IST on a pool whose single channel had
    earlier short bookings and a stale in_use flag."""
    now = datetime(2026, 10, 14, 7, 20, tzinfo=timezone.utc)          # 12:50 IST
    start = datetime(2026, 10, 14, 7, 22, tzinfo=timezone.utc)        # 12:52 IST
    end = datetime(2026, 10, 14, 7, 25, tzinfo=timezone.utc)          # 12:55 IST
    earlier = mk(app, "private_vc", start=now - timedelta(minutes=40), end=now - timedelta(minutes=5))
    set_status(app, earlier, "approved")                               # approved-but-ended test booking
    app.claim_pool_channel(9001, earlier)                              # stale in_use marker
    with app._cursor() as cur:
        avail = app._private_vc_availability(cur, start, end, now)
    assert avail["picked"]["channel_id"] == "9001"


def test_only_live_statuses_block_a_channel(app, env):
    s = NOW() + timedelta(hours=2)
    e = s + timedelta(hours=1)
    for status in ("declined", "expired", "cancelled", "completed", "pending"):
        r = mk(app, "private_vc", start=s, end=e)
        set_status(app, r, status)
    with app._cursor() as cur:
        assert app._private_vc_availability(cur, s, e, NOW())["picked"]["channel_id"] == "9001"
    live = mk(app, "private_vc", start=s, end=e)
    set_status(app, live, "approved")
    with app._cursor() as cur:
        avail = app._private_vc_availability(cur, s, e, NOW())
    assert avail["picked"] is None and avail["holder_ids"][("9001", s, e)] == live
    assert "next free slot" in app._no_private_vc_message(avail, s, e)


def test_overlap_refused_with_suggestion_and_never_double_assigned(app, env):
    s = NOW() + timedelta(hours=2)
    a = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    b = mk(app, "private_vc", start=s + timedelta(minutes=30), end=s + timedelta(hours=2), user="11")
    assert app.approve_reservation(a, "x", "private")[0] == "ok"
    res, info = app.approve_reservation(b, "x", "private")
    assert res == "no_channel" and info["suggestion"] == s + timedelta(hours=1)
    assert app.get_reservation(b)["status"] == "pending"


def test_channel_reused_after_previous_reservation_ended(app, env):
    fb, guild, pool = env
    s = NOW() + timedelta(seconds=30)
    a = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    assert app.approve_reservation(a, "x", "private")[0] == "ok"
    run(app.reservation_tick())
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET start_time = NOW() - interval '2 hours', end_time = NOW() - interval '1 hour' WHERE id=%s", (a,))
    run(app.reservation_tick())
    assert app.get_pool_row(9001)["status"] == "available"
    s2 = NOW() + timedelta(seconds=30)
    b = mk(app, "private_vc", start=s2, end=s2 + timedelta(hours=1), user="11")
    assert app.approve_reservation(b, "x", "private")[1]["channel_id"] == "9001"
    run(app.reservation_tick())
    assert app.get_pool_row(9001)["current_reservation_id"] == b


def test_lingering_previous_group_is_moved_out_when_locked_window_starts(app, env):
    fb, guild, pool = env
    leftover = FakeMember(999)
    pool._m.append(leftover)
    s = NOW() + timedelta(seconds=30)
    r = mk(app, "private_vc", start=s, end=s + timedelta(hours=1))
    app.approve_reservation(r, "x", "private")
    run(app.reservation_tick())
    assert leftover.moved


def test_concurrent_approvals_never_share_a_channel(app, env):
    import threading
    s = NOW() + timedelta(hours=3)
    ids = [mk(app, "private_vc", start=s, end=s + timedelta(hours=1), user=str(40 + i)) for i in range(6)]
    results = []
    def go(rid):
        results.append((rid, app.approve_reservation(rid, "x", "private")))
    threads = [threading.Thread(target=go, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r[1][0] for r in results).count("ok") == 1      # one pool channel -> exactly one winner
    assert sum(1 for r in results if r[1][0] == "no_channel") == 5


def test_booking_time_check_uses_ist_boundaries(app, env):
    # 12:52 IST on the reported date is 07:22 UTC; a clash expressed in UTC must be seen
    base = datetime(2026, 10, 14, 7, 22, tzinfo=timezone.utc)
    a = mk(app, "private_vc", start=base, end=base + timedelta(minutes=3))
    set_status(app, a, "approved")
    with app._cursor() as cur:
        clash = app._private_vc_availability(cur, base + timedelta(minutes=2), base + timedelta(minutes=5), base - timedelta(hours=1))
        after = app._private_vc_availability(cur, base + timedelta(minutes=3), base + timedelta(minutes=6), base - timedelta(hours=1))
    assert clash["picked"] is None
    assert after["picked"]["channel_id"] == "9001"


class _Perms:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _PermChannel:
    def __init__(self, perms):
        self._p = perms

    def permissions_for(self, me):
        return self._p


class _PermGuild:
    me = "bot"

    def __init__(self, perms):
        self._c = _PermChannel(perms)

    def get_channel(self, cid):
        return self._c


def test_permission_diagnostic_lists_missing_permissions(app):
    full = _Perms(view_channel=True, connect=True, manage_channels=True, manage_roles=True, move_members=True)
    assert app._pool_channel_missing_permissions(_PermGuild(full), 1) == []
    lacking = _Perms(view_channel=True, connect=True, manage_channels=False, manage_roles=False, move_members=True)
    assert app._pool_channel_missing_permissions(_PermGuild(lacking), 1) == ["Manage Channels", "Manage Permissions"]


# ---------- Issues 1 & 3: booking forms + flexible (quarter-hour) times ----------

def _form(app):
    return app.PrivateVCBookingView(_fake_member(app, admin=False))


def _future_date(app, days=2):
    return (NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS) + timedelta(days=days)).date()


def test_private_form_builds_valid_discord_components(app, env):
    form = _form(app)
    rows = [r["components"] for r in form.to_components()]
    assert len(rows) == 2 and len(rows[0]) == 1 and len(rows[1]) == 4
    assert [c.label for c in form.children if isinstance(c, discord.ui.Button)] == ["Name & Reason", "Date & time", "Locked", "Submit"]
    form.member_select.default_values = [discord.SelectDefaultValue(id=5, type=discord.SelectDefaultValueType.user)]
    form.to_components()


def test_existing_form_builds_valid_discord_components(app, env):
    form = app.ExistingVCBookingFormView(_fake_member(app, admin=False))
    rows = [r["components"] for r in form.to_components()]
    assert len(rows) == 2 and len(rows[1]) == 5
    assert [c.label for c in form.children if isinstance(c, discord.ui.Button)] == ["Date & time", "Members", "Reason", "Lock", "Submit"]


@pytest.mark.parametrize("make", [lambda a: a.PrivateVCBookingView(_fake_member(a, admin=False)),
                                  lambda a: a.ExistingVCBookingFormView(_fake_member(a, admin=False))])
def test_time_picker_components_and_quarter_hours(app, env, make):
    form = make(app)
    form.date_value = _future_date(app)
    picker = app.FlexibleTimePickerView(form)
    rows = [r["components"] for r in picker.to_components()]
    assert len(rows) == 5                                   # date, period, start, end, buttons
    assert all(len(select_row) == 1 for select_row in rows[:4]) and len(rows[4]) == 2
    picker.period_index = 3                                  # evening
    picker.rebuild()
    starts = [o.label for o in picker.start_select.options]
    assert len(starts) == 24 and starts[:5] == ["6:00 PM", "6:15 PM", "6:30 PM", "6:45 PM", "7:00 PM"]
    form.start_min = 19 * 60 + 15
    picker.rebuild()
    ends = [o.label for o in picker.end_select.options]
    assert ends[:3] == ["7:30 PM \u00b7 15m", "7:45 PM \u00b7 30m", "8:00 PM \u00b7 45m"]
    assert len(picker.end_select.options) <= 25


def test_time_picker_selection_flow_updates_the_form(app, env):
    form = _form(app)
    form.date_value = _future_date(app)
    picker = app.FlexibleTimePickerView(form)
    picker.period_index = 3
    picker.rebuild()

    class Sel:  # stands in for a chosen dropdown value
        def __init__(self, v):
            self.values = [v]

    async def choose(select, value):
        select._values = [value]
        i = FakeInteraction(FakeMember(1))
        async def edit_message(**kw): i.response.messages.append("edited")
        i.response.edit_message = edit_message
        select.__class__.values = property(lambda self: self._values)
        await select.callback(i)
    run(choose(picker.start_select, str(19 * 60 + 15)))
    run(choose(picker.end_select, str(19 * 60 + 45)))
    assert (form.start_min, form.end_min) == (19 * 60 + 15, 19 * 60 + 45)
    assert "7:15 PM \u2192 7:45 PM (30m)" in app._time_summary(form)[1]
    # moving the start later than the end clears the end
    run(choose(picker.start_select, str(20 * 60)))
    assert form.start_min == 20 * 60 and form.end_min is None


def test_time_picker_today_hides_past_slots_and_exact_times_validate(app, env):
    form = _form(app)
    picker = app.FlexibleTimePickerView(form)
    now_local = NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS)
    now_min = now_local.hour * 60 + now_local.minute
    for p in range(4):
        picker.period_index = p
        picker.rebuild()
        assert all(int(o.value) > now_min for o in picker.start_select.options if o.value != "none")

    async def submit(start, end):
        modal = app.ExactTimeModal(picker)
        modal.start_text._value, modal.end_text._value = start, end
        i = FakeInteraction(FakeMember(1))
        async def edit_message(**kw): i.response.messages.append("edited")
        i.response.edit_message = edit_message
        await modal.on_submit(i)
        return i.response.messages[0]
    form.date_value = _future_date(app)
    assert run(submit("7:20 PM", "9:05 PM")) == "edited"
    assert (form.start_min, form.end_min) == (19 * 60 + 20, 21 * 60 + 5)
    assert "End time" in run(submit("7:20 PM", "garbage"))
    assert "after the start" in run(submit("9 PM", "8 PM"))
    assert run(submit("11:30 PM", "12 AM")) == "edited" and form.end_min == 24 * 60   # midnight end
    form.date_value = app._local_today()
    assert "already passed" in run(submit("12:00 AM", "12:15 AM"))


def test_private_form_submit_validation_messages(app, env):
    form = _form(app)
    form.date_value = _future_date(app)
    cases = [(dict(), "name"), (dict(vc_name="Gaming"), "start time"),
             (dict(vc_name="Gaming", start_min=600), "end time")]
    for attrs, expect in cases:
        for k, v in attrs.items():
            setattr(form, k, v)
        i = FakeInteraction(_fake_member(app, admin=False), guild=env[1])
        run(form.submit.callback(i))
        assert expect in i.response.messages[0].lower()


def test_private_form_submit_creates_quarter_hour_ist_reservation(app, env):
    fb, guild, pool = env
    requester = FakeMember(77)
    requester.display_name = "Req"
    form = app.PrivateVCBookingView(requester)
    form.date_value = _future_date(app)
    form.vc_name, form.start_min, form.end_min = "\U0001F3AE Gaming", 19 * 60 + 15, 20 * 60 + 45
    form.selected_members = {"123456789012345678": "Pal"}
    guild.id = 1
    i = FakeInteraction(requester, guild=guild)
    run(form.submit.callback(i))
    assert "submitted" in i.response.messages[0].lower()
    with app._cursor() as cur:
        cur.execute("SELECT * FROM reservations WHERE requester_id='77'")
        row = cur.fetchone()
        cur.execute("SELECT user_id FROM reservation_members WHERE reservation_id=%s ORDER BY user_id", (row["id"],))
        members = [r["user_id"] for r in cur.fetchall()]
    assert row["status"] == "pending" and row["reservation_type"] == "private_vc" and row["lock_state"] == "locked"
    assert members == ["123456789012345678", "77"]
    local = lambda dt: (dt + timedelta(hours=app.TIMEZONE_OFFSET_HOURS)).strftime("%H:%M")
    assert (local(row["start_time"]), local(row["end_time"])) == ("19:15", "20:45")
    assert row["start_time"].utcoffset() == timedelta(0)                       # stored as UTC


def test_existing_form_submit_quarter_hour_and_overlap_protection(app, env):
    fb, guild, pool = env
    guild.id = 1
    d = _future_date(app)

    async def book(uid, start_min, end_min):
        requester = FakeMember(uid)
        requester.display_name = f"U{uid}"
        form = app.ExistingVCBookingFormView(requester)
        form.channel_id, form.channel_name = 555, "room"
        form.date_value, form.start_min, form.end_min = d, start_min, end_min
        i = FakeInteraction(requester, guild=guild)
        await form.submit.callback(i)
        return i.response.messages[0]

    assert "submitted" in run(book(201, 19 * 60 + 15, 20 * 60)).lower()
    with app._cursor() as cur:
        cur.execute("SELECT id FROM reservations WHERE requester_id='201'")
        rid = cur.fetchone()["id"]
        cur.execute("UPDATE reservations SET created_at = created_at - interval '2 hours' WHERE requester_id='201'")
    assert app.approve_reservation(rid, "x", "existing")[0] == "ok"
    # overlapping by a single quarter-hour is refused for the same channel...
    assert "already reserved" in run(book(202, 19 * 60 + 45, 20 * 60 + 30))
    # ...touching at the boundary is allowed
    assert "submitted" in run(book(203, 20 * 60, 20 * 60 + 30)).lower()


def test_quarter_hour_reservation_lifecycle_boundaries(app, env):
    """Window edges at non-hourly times, relative to real 'now' with second-level precision."""
    fb, guild, pool = env
    s = NOW().replace(second=0, microsecond=0) + timedelta(minutes=47)
    e = s + timedelta(minutes=30)
    r = mk(app, start=s, end=e)
    app.approve_reservation(r, "x", "existing")
    f = lambda at: run(app.find_active_reservation_for_channel(555, at))
    assert f(s - timedelta(seconds=1)) is None
    assert f(s)["id"] == r
    assert f(e - timedelta(seconds=1))["id"] == r
    assert f(e) is None
    # session tagging follows the same edges
    with app._cursor() as cur:
        cur.execute("""SELECT id FROM reservations WHERE assigned_channel_id='555' AND status IN ('approved','active')
                       AND start_time <= %s AND end_time > %s""", (e, e))
        assert cur.fetchone() is None
    # a back-to-back booking starting at the exact end time is conflict-free
    nxt = mk(app, start=e, end=e + timedelta(minutes=15), user="11")
    assert app.approve_reservation(nxt, "x", "existing")[0] == "ok"
    # one minute of overlap is not
    clash = mk(app, start=e + timedelta(minutes=14), end=e + timedelta(minutes=45), user="12")
    assert app.approve_reservation(clash, "x", "existing")[0] == "conflict"


def test_scheduler_ends_quarter_hour_reservation_exactly_at_end(app, env):
    fb, guild, pool = env
    now = NOW()
    r = mk(app, start=now - timedelta(minutes=30), end=now + timedelta(seconds=30))
    app.approve_reservation(r, "x", "existing")
    app.update_reservation_status(r, "active")
    run(app.reservation_tick())
    assert app.get_reservation(r)["status"] == "active"            # not yet over
    with app._cursor() as cur:
        cur.execute("UPDATE reservations SET end_time = NOW() - interval '1 second' WHERE id=%s", (r,))
    run(app.reservation_tick())
    assert app.get_reservation(r)["status"] == "completed"


def test_private_allocation_with_non_hourly_windows(app, env):
    base = NOW().replace(second=0, microsecond=0) + timedelta(hours=3, minutes=7)
    a = mk(app, "private_vc", start=base, end=base + timedelta(minutes=23))
    assert app.approve_reservation(a, "x", "private")[0] == "ok"
    inside = mk(app, "private_vc", start=base + timedelta(minutes=22), end=base + timedelta(minutes=40), user="11")
    assert app.approve_reservation(inside, "x", "private")[0] == "no_channel"
    adjacent = mk(app, "private_vc", start=base + timedelta(minutes=23), end=base + timedelta(minutes=38), user="12")
    assert app.approve_reservation(adjacent, "x", "private")[1]["channel_id"] == "9001"


# ---------- existing entry points still work ----------

def test_menu_and_reserve_entry_points(app, env):
    ids = {c.custom_id for c in app.VCMenuView().children}
    assert ids == {"vc_menu_reservations", "vc_menu_who_was_with_me", "vc_menu_my_report", "vc_menu_admin_list"}
    panel = {c.custom_id for c in app.ReservationBookingPanelView().children}
    assert panel == {"reservation_panel_existing_vc", "reservation_panel_private_vc"}
    i = FakeInteraction(FakeMember(1))
    run(app.run_vc_reserve(i))
    assert "reserve" in i.response.messages[0].lower()
    app.ReservationTypeChoiceView().to_components()


def test_approval_views_keep_their_custom_ids(app, env):
    ex = {c.custom_id for c in app.ReservationApprovalViewExisting(5).children}
    assert ex == {"resv_give_existing_5", "resv_give_private_5", "resv_decline_5"}
    pv = {c.custom_id for c in app.ReservationApprovalViewPrivate(5).children}
    assert pv == {"resv_approve_private_5", "resv_decline_5"}
