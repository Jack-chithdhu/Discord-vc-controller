"""
Contract tests for GET /api/points-leaderboard (new, read-only, password protected).

Written before the endpoint. It exposes member names and point balances, so unlike
the older read endpoints it needs the panel password. It must never write.
Needs TEST_DATABASE_URL; skipped otherwise.
"""
import importlib.util, os, sys

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = "points-test-password"
AUTH = {"X-PW": PASSWORD}
BIG = 10 ** 15                      # sits above any real balance in a test database


class Member:
    def __init__(self, name): self.display_name = name


class Guild:
    def __init__(self, members): self._m = members
    def get_member(self, uid): return self._m.get(uid)


class Bot:
    def __init__(self, guild): self._g = guild
    def get_guild(self, _id): return self._g


@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_points_api", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    return mod


@pytest.fixture()
def client(app, monkeypatch):
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    with app._cursor() as cur:
        cur.execute("DELETE FROM points WHERE user_id LIKE '9990000%'")
        for uid, bal in (("99900000001", BIG + 30), ("99900000002", BIG + 20), ("99900000003", BIG + 10)):
            cur.execute("INSERT INTO points (user_id, balance) VALUES (%s, %s)", (uid, bal))
    yield app.app.test_client()
    with app._cursor() as cur:
        cur.execute("DELETE FROM points WHERE user_id LIKE '9990000%'")


def test_it_needs_the_panel_password(client):
    for headers in ({}, {"X-PW": "wrong"}, {"X-PW": ""}):
        r = client.get("/api/points-leaderboard", headers=headers)
        assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


def test_the_password_in_a_query_string_still_works_like_other_routes(client):
    assert client.get(f"/api/points-leaderboard?pw={PASSWORD}").status_code == 200


def test_shape_order_and_rank(client, app, monkeypatch):
    monkeypatch.setattr(app, "bot", Bot(Guild({99900000001: Member("Ann")})))
    d = client.get("/api/points-leaderboard?limit=3", headers=AUTH).get_json()
    assert set(d) == {"entries", "limit", "feature_enabled"} and d["limit"] == 3
    assert isinstance(d["feature_enabled"], bool)
    e = d["entries"]
    assert [x["user_id"] for x in e] == ["99900000001", "99900000002", "99900000003"]
    assert [x["rank"] for x in e] == [1, 2, 3]
    assert [x["balance"] for x in e] == [BIG + 30, BIG + 20, BIG + 10]
    for x in e:
        assert set(x) == {"rank", "user_id", "name", "balance"} and isinstance(x["user_id"], str)


def test_names_come_from_the_server_and_are_null_when_unknown_or_the_bot_is_down(client, app, monkeypatch):
    monkeypatch.setattr(app, "bot", Bot(Guild({99900000001: Member("Ann")})))
    e = client.get("/api/points-leaderboard?limit=3", headers=AUTH).get_json()["entries"]
    assert e[0]["name"] == "Ann" and e[1]["name"] is None
    monkeypatch.setattr(app, "bot", None)
    e = client.get("/api/points-leaderboard?limit=3", headers=AUTH).get_json()["entries"]
    assert [x["name"] for x in e] == [None, None, None]


def test_limit_defaults_to_ten_and_is_validated_without_a_500(client):
    assert client.get("/api/points-leaderboard", headers=AUTH).get_json()["limit"] == 10
    for bad, msg in (("abc", "limit must be a whole number"), ("2.5", "limit must be a whole number"),
                     ("0", "limit must be between 1 and 50"), ("-1", "limit must be between 1 and 50"),
                     ("51", "limit must be between 1 and 50")):
        r = client.get(f"/api/points-leaderboard?limit={bad}", headers=AUTH)
        assert r.status_code == 400 and r.get_json() == {"error": msg}, bad
    assert client.get("/api/points-leaderboard?limit=50", headers=AUTH).status_code == 200


def test_it_never_changes_the_data(client, app):
    def snapshot():
        with app._cursor() as cur:
            cur.execute("SELECT user_id, balance, last_daily_claim FROM points ORDER BY user_id")
            return cur.fetchall()
    before = snapshot()
    client.get("/api/points-leaderboard?limit=50", headers=AUTH)
    assert snapshot() == before


def test_only_get_is_allowed(client):
    assert client.post("/api/points-leaderboard", json={}, headers=AUTH).status_code == 405


def test_database_down_is_a_clean_503(client, app, monkeypatch):
    monkeypatch.setattr(app, "is_enabled", lambda: False)
    r = client.get("/api/points-leaderboard", headers=AUTH)
    assert r.status_code == 503 and r.get_json() == {"error": "database not connected"}
