"""
Success-path contract tests for the voice-operation API.

test_web_contract.py pins authorization and validation errors. This file pins
what the routes RETURN when they work (keys and value types), because the new
Voice Operations page reads exactly these fields. The Discord client is replaced
by small fakes, so nothing talks to Discord. Needs TEST_DATABASE_URL (the lock
routes write to the database); skipped otherwise.
"""
import asyncio, importlib.util, os, sys

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = "shape-test-password"
AUTH = {"X-PW": PASSWORD}


class Voice:
    def __init__(self, mute=False, deaf=False):
        self.mute, self.deaf = mute, deaf


class Avatar:
    url = "https://cdn.example/avatar.png"


class Member:
    def __init__(self, uid, name, in_voice=True):
        self.id, self.display_name, self.display_avatar = uid, name, Avatar()
        self.voice = Voice() if in_voice else None

    async def edit(self, mute=None, deafen=None):
        if mute is not None:
            self.voice.mute = mute
        if deafen is not None:
            self.voice.deaf = deafen


class Channel:
    def __init__(self, cid, name, members=()):
        self.id, self.name, self.members, self.category_id = cid, name, list(members), None


class Guild:
    def __init__(self, channels, members):
        self._c = {c.id: c for c in channels}
        self._m = {m.id: m for m in members}

    def get_channel(self, cid): return self._c.get(cid)
    def get_member(self, uid): return self._m.get(uid)


class Bot:
    def __init__(self, guild): self._g = guild
    def get_guild(self, _id): return self._g
    def is_ready(self): return True


@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_voice_shapes", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    return mod


@pytest.fixture()
def world(app, monkeypatch):
    people = [Member(101, "Ann"), Member(102, "Ben"), Member(103, "Cy"), Member(104, "Di")]
    lobby = Channel(11, "Lobby", people)
    team_a, team_b = Channel(12, "Team A"), Channel(13, "Team B")
    guild = Guild([lobby, team_a, team_b], people + [Member(105, "Idle", in_voice=False)])
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    monkeypatch.setattr(app, "bot", Bot(guild))
    monkeypatch.setattr(app, "run_coro", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(app, "ALLOWED_CATEGORY_ID", "")

    async def list_channels():
        return [{"id": str(c.id), "name": c.name, "member_count": len(c.members), "locked": c.id in app.channel_locks}
                for c in (lobby, team_a, team_b)]

    async def get_members(cid):
        c = guild.get_channel(cid)
        return None if c is None else list(c.members)

    async def move_members(members, cid):
        return [app.member_info(m) for m in members], []

    async def disconnect_members(members):
        return [app.member_info(m) for m in members], []

    monkeypatch.setattr(app, "list_voice_channels", list_channels)
    monkeypatch.setattr(app, "get_channel_members", get_members)
    monkeypatch.setattr(app, "move_members", move_members)
    monkeypatch.setattr(app, "disconnect_members", disconnect_members)
    monkeypatch.setattr(app, "last_split", {"team_a": [], "team_b": []})
    yield app.app.test_client(), people, guild
    app.channel_locks.clear(); app.locked_members.clear()
    app.clear_lock_channels(); app.clear_lock_members()


MEMBER_KEYS = {"id", "name", "avatar", "muted", "deafened"}


def test_channels_shape(world):
    c, *_ = world
    d = c.get("/api/channels").get_json()
    assert set(d) == {"channels"} and len(d["channels"]) == 3
    for ch in d["channels"]:
        assert set(ch) == {"id", "name", "member_count", "locked"}
        assert isinstance(ch["id"], str) and isinstance(ch["member_count"], int) and isinstance(ch["locked"], bool)


def test_lobby_members_shape_and_unknown_lobby(world):
    c, *_ = world
    d = c.get("/api/lobby_members?lobby_id=11").get_json()
    assert len(d["members"]) == 4
    for m in d["members"]:
        assert set(m) == MEMBER_KEYS and isinstance(m["id"], str) and isinstance(m["muted"], bool)
    r = c.get("/api/lobby_members?lobby_id=999")
    assert r.status_code == 400 and r.get_json() == {"error": "Lobby channel not found"}


def test_split_returns_two_disjoint_teams_covering_everyone(world, app):
    c, people, _ = world
    d = c.post("/api/split", json={"lobby_id": "11"}, headers=AUTH).get_json()
    a, b = {m["id"] for m in d["team_a"]}, {m["id"] for m in d["team_b"]}
    assert set(d) == {"team_a", "team_b"} and not (a & b) and a | b == {str(p.id) for p in people}
    assert abs(len(a) - len(b)) <= 1
    assert set(app.last_split["team_a"]) == {int(i) for i in a}


def test_split_needs_two_people(world, app):
    c, people, guild = world
    guild.get_channel(11).members[:] = people[:1]
    r = c.post("/api/split", json={"lobby_id": "11"}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "Need at least 2 people in the lobby VC"}


def test_move_with_explicit_members_returns_moved_and_failed(world):
    c, *_ = world
    r = c.post("/api/move/a", json={"channel_id": "12", "member_ids": ["101", "102"]}, headers=AUTH)
    d = r.get_json()
    assert r.status_code == 200 and set(d) == {"moved", "failed"} and d["failed"] == []
    assert [m["name"] for m in d["moved"]] == ["Ann", "Ben"]


def test_move_without_anyone_assigned_is_a_clear_error(world):
    c, *_ = world
    r = c.post("/api/move/b", json={"channel_id": "13"}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "No players selected for this team"}


def test_disconnect_shape_and_nobody_in_voice(world):
    c, *_ = world
    d = c.post("/api/disconnect", json={"member_ids": ["101"]}, headers=AUTH).get_json()
    assert set(d) == {"disconnected", "failed"} and d["disconnected"][0]["name"] == "Ann"
    r = c.post("/api/disconnect", json={"member_ids": ["105"]}, headers=AUTH)
    assert r.status_code == 400 and "currently in a voice channel" in r.get_json()["error"]


def test_regroup_and_rematch_shapes(world, app):
    c, people, guild = world
    d = c.post("/api/regroup", json={"from_channel_ids": ["12", "13"], "to_channel_id": "11"}, headers=AUTH).get_json()
    assert set(d) == {"moved", "failed"}
    r = c.post("/api/rematch", json={"team_a_channel": "12", "team_b_channel": "13"}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "Both team channels are empty"}
    guild.get_channel(12).members[:] = people[:2]
    d = c.post("/api/rematch", json={"team_a_channel": "12", "team_b_channel": "13"}, headers=AUTH).get_json()
    assert d == {"status": "swapped", "failed": []}


def test_voice_toggle_shape_and_errors(world):
    c, *_ = world
    d = c.post("/api/voice_toggle", json={"member_id": "101", "action": "mute"}, headers=AUTH).get_json()
    assert d == {"member_id": "101", "action": "mute", "state": True}
    d = c.post("/api/voice_toggle", json={"member_id": "101", "action": "mute"}, headers=AUTH).get_json()
    assert d["state"] is False
    r = c.post("/api/voice_toggle", json={"member_id": "105", "action": "deafen"}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "Player is not in a voice channel"}
    r = c.post("/api/voice_toggle", json={"member_id": "999", "action": "deafen"}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "Member not found"}


def test_lock_status_and_unlock_shapes(world, app):
    c, *_ = world
    assert c.get("/api/lock_status").get_json() == {
        "active": False, "channels": [], "total_locked_members": 0, "violation_action": app.LOCK_VIOLATION_ACTION}
    d = c.post("/api/lock", json={"channel_ids": ["11"]}, headers=AUTH).get_json()
    assert d == {"locked_channel_ids": ["11"]}
    s = c.get("/api/lock_status").get_json()
    assert s["active"] is True and s["total_locked_members"] == 4
    assert s["channels"] == [{"id": "11", "name": "Lobby", "locked_member_count": 4}]
    assert c.post("/api/unlock", json={"channel_ids": ["11"]}, headers=AUTH).get_json() == {"locked_channel_ids": []}
