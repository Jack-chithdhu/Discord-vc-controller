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


# ---------- Issue 1: Private VC booking form ----------

def _form(app):
    return app.PrivateVCBookingView(_fake_member(app, admin=False))


def test_private_form_builds_valid_discord_components(app, env):
    form = _form(app)
    assert len(form.children) == 8  # 3 selects + user select + 4 buttons
    payload = form.to_components()
    rows = [r["components"] for r in payload]
    assert len(rows) == 5 and all(len(r) >= 1 for r in rows)
    assert len(rows[4]) == 4
    form.member_select.default_values = [discord.SelectDefaultValue(id=5, type=discord.SelectDefaultValueType.user)]
    form.to_components()  # still serialisable


def test_private_form_time_options_follow_selection(app, env):
    form = _form(app)
    form.date_value = (NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS) + timedelta(days=3)).date()
    form.rebuild_time_options()
    assert len(form.start_select.options) == 24
    form.start_min = 20 * 60
    form.end_min = 19 * 60
    form.rebuild_time_options()
    assert form.end_min is None                                      # invalid end cleared
    assert [o.value for o in form.end_select.options] == ["21", "22", "23"]
    form.start_min = 12 * 60 + 52                                     # exact time via the modal
    form.rebuild_time_options()
    assert form.end_select.options[0].value == "13"
    assert not any(o.default for o in form.start_select.options)


def test_private_form_today_hides_past_hours(app, env):
    form = _form(app)
    now_local = NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS)
    assert form.date_value == now_local.date()
    values = [int(o.value) for o in form.start_select.options if o.value != "none"]
    assert all(v > now_local.hour for v in values)


def test_private_form_submit_validation_messages(app, env):
    form = _form(app)
    form.date_value = (NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS) + timedelta(days=2)).date()
    cases = [(dict(), "name"), (dict(vc_name="Gaming"), "start time"),
             (dict(vc_name="Gaming", start_min=600), "end time")]
    for attrs, expect in cases:
        for k, v in attrs.items():
            setattr(form, k, v)
        i = FakeInteraction(_fake_member(app, admin=False), guild=env[1])
        run(form.submit.callback(i))
        assert expect in i.response.messages[0].lower()


def test_private_form_submit_creates_pending_private_reservation(app, env):
    fb, guild, pool = env
    requester = FakeMember(77)
    requester.display_name = "Req"
    form = app.PrivateVCBookingView(requester)
    form.date_value = (NOW() + timedelta(hours=app.TIMEZONE_OFFSET_HOURS) + timedelta(days=2)).date()
    form.vc_name, form.start_min, form.end_min = "\U0001F3AE Gaming", 19 * 60, 20 * 60 + 30
    form.members = {"123456789012345678": "Pal"}
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
    # 19:00 IST == 13:30 UTC
    assert (row["start_time"] + timedelta(hours=app.TIMEZONE_OFFSET_HOURS)).strftime("%H:%M") == "19:00"


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
