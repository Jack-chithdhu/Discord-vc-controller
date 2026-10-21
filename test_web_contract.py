"""
API contract tests for the existing website routes.

These pin what the control panel and the profile pages rely on TODAY: which
routes exist, which need the shared password, the status codes, and the JSON
keys. They were written before the website redesign so that any later change
to an existing route shows up as a failing test instead of a surprise.

Needs a real PostgreSQL (TEST_DATABASE_URL, e.g. postgresql://postgres:pw@localhost/vct);
skipped otherwise. No Discord connection is made: the tests use Flask's test client.
"""
import importlib.util, os, sys

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")

HERE = os.path.dirname(os.path.abspath(__file__))
PASSWORD = "contract-test-password"


def _load_app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_web_contract", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app():
    mod = _load_app()
    assert mod.init_db()
    return mod


@pytest.fixture()
def locked(app, monkeypatch):
    """The site with a password set (production-like)."""
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    return app.app.test_client()


@pytest.fixture()
def open_site(app, monkeypatch):
    """The site with NO password configured."""
    monkeypatch.setattr(app, "WEB_PASSWORD", "")
    return app.app.test_client()


AUTH = {"X-PW": PASSWORD}

# Every route that changes something or reads settings: all need the password.
PRIVILEGED = [
    ("post", "/api/split"), ("post", "/api/move/a"), ("post", "/api/move/b"),
    ("post", "/api/disconnect"), ("post", "/api/regroup"), ("post", "/api/rematch"),
    ("post", "/api/voice_toggle"), ("post", "/api/lock"), ("post", "/api/unlock"),
    ("get", "/api/toggles"), ("post", "/api/toggles/gambling"),
    ("get", "/api/birthday-reminder-interval"), ("post", "/api/birthday-reminder-interval"),
    ("get", "/api/reservation-settings"), ("post", "/api/reservation-settings"),
]


def call(client, method, path, **kw):
    return getattr(client, method)(path, **kw)


# ---------------------------------------------------------------- authorization
@pytest.mark.parametrize("method,path", PRIVILEGED)
def test_privileged_routes_refuse_a_missing_password(locked, method, path):
    r = call(locked, method, path, json={})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("method,path", PRIVILEGED)
def test_privileged_routes_refuse_a_wrong_password(locked, method, path):
    r = call(locked, method, path, json={}, headers={"X-PW": "nope"})
    assert r.status_code == 401 and r.get_json() == {"error": "unauthorized"}


@pytest.mark.parametrize("method,path", PRIVILEGED)
def test_privileged_routes_accept_the_password_in_the_header_or_query(locked, method, path):
    assert call(locked, method, path, json={}, headers=AUTH).status_code != 401
    assert call(locked, method, f"{path}?pw={PASSWORD}", json={}).status_code != 401


def test_empty_password_header_is_not_accepted_when_a_password_is_set(locked):
    assert locked.get("/api/toggles", headers={"X-PW": ""}).status_code == 401


def test_without_a_configured_password_nothing_is_locked(open_site):
    assert open_site.get("/api/toggles").status_code == 200
    assert open_site.post("/api/split", json={}).status_code == 400      # validation, not 401


# ---------------------------------------------------------------- input validation on voice actions
VALIDATION = [
    ("/api/split", {}, "lobby_id required"),
    ("/api/move/a", {}, "channel_id required"),
    ("/api/move/zzz", {}, "bad team"),
    ("/api/disconnect", {}, "member_ids required"),
    ("/api/lock", {}, "channel_ids required"),
]


@pytest.mark.parametrize("path,body,message", VALIDATION)
def test_voice_actions_validate_input_before_touching_discord(locked, path, body, message):
    r = locked.post(path, json=body, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": message}


@pytest.mark.parametrize("path", ["/api/regroup", "/api/rematch", "/api/voice_toggle"])
def test_other_voice_actions_reject_empty_requests(locked, path):
    r = locked.post(path, json={}, headers=AUTH)
    assert r.status_code == 400 and "error" in r.get_json()


def test_non_json_body_is_treated_as_empty_not_a_crash(locked):
    r = locked.post("/api/split", data="not json", headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "lobby_id required"}


def test_unlock_with_nothing_locked_succeeds_with_the_documented_key(locked):
    r = locked.post("/api/unlock", json={}, headers=AUTH)
    assert r.status_code == 200 and "locked_channel_ids" in r.get_json()


# ---------------------------------------------------------------- settings routes
def test_toggle_list_shape(app, locked):
    d = locked.get("/api/toggles", headers=AUTH).get_json()
    assert [t["key"] for t in d["toggles"]] == list(app.TOGGLE_KEYS)
    for t in d["toggles"]:
        assert set(t) == {"key", "description", "enabled"} and isinstance(t["enabled"], bool)


def test_toggle_set_round_trip_and_unknown_key(app, locked):
    before = app.get_toggle("gambling")
    try:
        r = locked.post("/api/toggles/gambling", json={"enabled": not before}, headers=AUTH)
        assert r.status_code == 200 and r.get_json() == {"key": "gambling", "enabled": (not before)}
        assert app.get_toggle("gambling") is (not before)
    finally:
        app.set_toggle("gambling", before)
    r = locked.post("/api/toggles/not-a-feature", json={"enabled": True}, headers=AUTH)
    assert r.status_code == 400 and r.get_json() == {"error": "unknown toggle key"}


def test_birthday_interval_contract(app, locked):
    before = app.get_birthday_reminder_interval_hours()
    try:
        r = locked.post("/api/birthday-reminder-interval", json={"value": 2, "unit": "days"}, headers=AUTH)
        assert r.status_code == 200 and r.get_json() == {"hours": 48.0}
        got = locked.get("/api/birthday-reminder-interval", headers=AUTH).get_json()
        assert got == {"value": 2.0, "unit": "days"}
        for bad, msg in [({"value": "x", "unit": "days"}, "invalid value"),
                         ({"value": 1, "unit": "weeks"}, "unit must be minutes, hours, days, or years"),
                         ({"value": 0, "unit": "days"}, "value must be greater than 0")]:
            r = locked.post("/api/birthday-reminder-interval", json=bad, headers=AUTH)
            assert r.status_code == 400 and r.get_json() == {"error": msg}
    finally:
        app.set_birthday_reminder_interval_hours(before)


RESERVATION_KEYS = {"auto_expire_enabled", "cooldown_minutes", "duplicate_protection_enabled",
                    "expire_after_hours", "max_active", "max_pending", "max_per_day", "spam_protection_enabled"}


def test_reservation_settings_contract(app, locked):
    before = locked.get("/api/reservation-settings", headers=AUTH).get_json()
    assert set(before) == RESERVATION_KEYS
    try:
        r = locked.post("/api/reservation-settings", json={"max_per_day": 7}, headers=AUTH)
        assert r.status_code == 200 and r.get_json()["max_per_day"] == 7 and set(r.get_json()) == RESERVATION_KEYS
        r = locked.post("/api/reservation-settings", json={"max_per_day": -1}, headers=AUTH)
        assert r.status_code == 400 and r.get_json() == {"error": "max_per_day can't be negative"}
        r = locked.post("/api/reservation-settings", json={"max_per_day": "abc"}, headers=AUTH)
        assert r.status_code == 400 and r.get_json() == {"error": "invalid value"}
        assert locked.get("/api/reservation-settings", headers=AUTH).get_json()["max_per_day"] == 7
    finally:
        locked.post("/api/reservation-settings", json={"max_per_day": before["max_per_day"]}, headers=AUTH)


# ---------------------------------------------------------------- public read-only routes (no password, by design)
PUBLIC_JSON = {
    "/api/voice_log": {"events", "source"},
    "/api/leaderboard": {"early_bird", "enabled", "longest_session", "night_owl", "period", "vc_time"},
    "/api/analytics/dashboard": {"channel_totals", "enabled", "hour_of_day_seconds", "note",
                                 "peak_concurrent", "period", "total_sessions_analyzed"},
    "/api/profiles": {"people"},
    "/api/vcdata/1/heatmap": {"days", "year", "years"},
    "/api/vcdata/1/hourmap": {"grid"},
    "/api/vcdata/1/companions": {"companions"},
    "/api/vcdata/1/achievements": {"badges", "enabled", "evaluated", "latest", "progress",
                                   "streak", "total_available", "total_unlocked"},
}


@pytest.mark.parametrize("path,keys", sorted(PUBLIC_JSON.items()))
def test_public_read_routes_need_no_password_and_keep_their_keys(locked, path, keys):
    r = locked.get(path)
    assert r.status_code == 200 and r.is_json
    assert keys <= set(r.get_json())


def test_leaderboard_accepts_each_period(locked):
    for period in ("all", "month", "week", "today"):
        r = locked.get(f"/api/leaderboard?period={period}")
        assert r.status_code == 200 and r.get_json()["period"] == period


def test_unknown_member_profile_is_404_json(locked):
    r = locked.get("/api/vcdata/1")
    assert r.status_code == 404 and "error" in r.get_json()


def test_day_breakdown_contract(locked):
    r = locked.get("/api/vcdata/1/day/2026-01-01")
    assert r.status_code == 200
    assert {"date", "sessions", "timeline", "total", "channels", "companions"} <= set(r.get_json())


def test_lobby_members_requires_a_lobby_id(locked):
    r = locked.get("/api/lobby_members")
    assert r.status_code == 400 and r.get_json() == {"error": "lobby_id required"}


def test_routes_that_need_the_discord_client_answer_with_json_errors_when_it_is_down(locked):
    """Without a connected bot these return a JSON error, never an HTML traceback."""
    for path in ("/api/channels", "/api/lock_status", "/api/lobby_members?lobby_id=1"):
        r = locked.get(path)
        assert r.is_json and "error" in r.get_json() and r.status_code >= 400


def test_ping_is_plain_and_unauthenticated(locked):
    r = locked.get("/ping")
    assert r.status_code == 200 and r.get_data(as_text=True) == "pong"


# ---------------------------------------------------------------- pages
def test_control_panel_page_and_its_password_box(app, monkeypatch):
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    html = app.app.test_client().get("/").get_data(as_text=True)
    assert "VC control" in html and "switchTab(" in html and "display: block" in html
    for tab in ("dashboard", "teams", "lock", "stats", "log", "settings"):
        assert f'id="view-{tab}"' in html
    monkeypatch.setattr(app, "WEB_PASSWORD", "")
    html = app.app.test_client().get("/").get_data(as_text=True)
    assert "display: none" in html.split("#pwbox", 1)[1][:80]


def test_control_panel_never_contains_secrets(app, monkeypatch):
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    html = app.app.test_client().get("/").get_data(as_text=True)
    assert PASSWORD not in html and "postgres" not in html.lower() and "DISCORD_TOKEN" not in html


def test_profile_pages_render(locked):
    for path in ("/profile/123", "/profiles"):
        r = locked.get(path)
        assert r.status_code == 200 and r.content_type.startswith("text/html")
    assert "no-store" in locked.get("/profile/123").headers["Cache-Control"]


def test_guide_assets_only_serves_catalog_files(locked):
    assert locked.get("/guide-assets/not-in-catalog.png").status_code == 404
