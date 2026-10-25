"""
Phase 4 checks for Community, Analytics, Administration and Reservations.

Static checks need no database. They guard the rules for pages that can change
bot settings: only known endpoints, every write is authenticated and confirmed,
the reservation editor sends only changed fields and re-reads the truth after a
failure, nothing builds HTML from server text, and the markup the scripts need exists.
"""
import os, re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


def read(*p):
    with open(os.path.join(HERE, *p), encoding="utf-8") as f:
        return f.read()


NAMES = ("admin", "reservations", "community", "analytics")
JS = {n: read("static", "js", n + ".js") for n in NAMES}
TPL = {"admin": "administration", "reservations": "reservations", "community": "community", "analytics": "analytics"}

ALLOWED = {
    "admin": {"/api/toggles", "/api/toggles/", "/api/birthday-reminder-interval"},
    "reservations": {"/api/reservation-settings", "/api/status"},
    "community": {"/api/leaderboard", "/api/profiles", "/api/points-leaderboard"},
    "analytics": {"/api/analytics/dashboard", "/api/status"},
}


def endpoints(js):
    return set(re.findall(r'"(/api/[a-z_/\-]*)', js))


@pytest.mark.parametrize("name", NAMES)
def test_each_script_calls_only_its_own_known_endpoints(name):
    assert endpoints(JS[name]) <= ALLOWED[name], endpoints(JS[name]) - ALLOWED[name]


@pytest.mark.parametrize("name", NAMES)
def test_no_script_builds_markup_from_text_or_evaluates_code_or_stores_data(name):
    js = JS[name]
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                "localStorage", "sessionStorage", "document.cookie", "console.", "?pw=", "&pw="):
        assert bad not in js, bad


def test_read_only_pages_never_write():
    for n in ("community", "analytics"):
        assert ".post(" not in JS[n], n


def test_every_privileged_call_sends_the_password_header():
    assert 'api.get("/api/toggles", { auth: true })' in JS["admin"] and 'api.get("/api/birthday-reminder-interval", { auth: true })' in JS["admin"]
    assert 'api.post(path, body, { auth: true })' in JS["admin"] and JS["admin"].count("api.post(") == 1
    assert 'api.get("/api/reservation-settings", { auth: true })' in JS["reservations"]
    assert 'api.post("/api/reservation-settings", c.values, { auth: true })' in JS["reservations"]
    assert 'api.get("/api/points-leaderboard?limit=10", { auth: true })' in JS["community"]


def test_admin_changes_are_confirmed_and_only_shown_after_the_server_answers():
    js = JS["admin"]
    assert "ui.confirm(" in js.split("function changeToggle", 1)[1]
    assert "inp.checked = !want;" in js                      # the switch does not move on its own
    assert js.index("send(\"/api/toggles/\"") > js.index("ui.confirm(")
    assert "if (!ok) return;" in js


def test_reservation_editor_sends_only_changed_fields_validates_and_rereads_after_failure():
    js = JS["reservations"]
    assert "ui.confirm(" in js and "if (!ok) return;" in js
    assert "c.values" in js and "n !== Number(saved[f])" in js            # only differing numbers
    assert "must be a whole number" in js and "must be 0 or more" in js    # server would truncate or reject these
    fail = js.split("function (err) {\n        busy = false;", 1)[1]
    assert "load();" in fail                                              # shows what the server really has


def test_points_page_handles_a_locked_session_without_calling_the_endpoint():
    js = JS["community"]
    assert "ui.onAccess(" in js and "onUsable: loadPoints" in js
    assert 'err.kind === "unauthorized"' in js and "VC.access.expire()" in js


def test_chart_follows_the_dataviz_rules():
    js = JS["analytics"]
    assert "Math.min(24, slot - 4)" in js                                  # bars never thicker than 24px, 2px+ gaps
    assert "Math.min(4, bh)" in js                                         # 4px rounded data end
    assert "View as table" in read("templates", "ui", "analytics.html")    # a table view of every value
    assert 'role: "img"' in js and "aria-label" in js


def ids_in(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


@pytest.mark.parametrize("name", NAMES)
def test_each_template_has_every_element_its_script_looks_up(name):
    html = read("templates", "ui", TPL[name] + ".html")
    wanted = set(re.findall(r'\$\("([a-z0-9\-]+)"\)', JS[name]))
    assert wanted, name
    missing = wanted - ids_in(html)
    assert not missing, missing
    assert f"/static/js/{name}.js" in html


def test_rv_field_ids_match_the_server_fields():
    html = read("templates", "ui", "reservations.html")
    fields = {"cooldown_minutes", "expire_after_hours", "max_pending", "max_per_day", "max_active",
              "spam_protection_enabled", "duplicate_protection_enabled", "auto_expire_enabled"}
    assert {f for f in fields if f'id="rv-{f}"' in html} == fields
    assert set(re.findall(r'"([a-z_]+_(?:enabled|minutes|hours)|max_[a-z_]+)"', JS["reservations"])) >= fields


def test_planned_items_are_labelled_and_no_unapproved_endpoint_is_called():
    r = read("templates", "ui", "reservations.html")
    for name in ("Reservation list", "Reservation history", "Approve or decline from the website"):
        assert "badge--planned" in r.split(name, 1)[1][:300] or '"planned"' in r.split(name, 1)[1][:200]
    assert '"planned"' in read("templates", "ui", "community.html") and '"planned"' in read("templates", "ui", "analytics.html")
    for js in JS.values():
        assert "/api/reservations" not in js and "/api/achievements" not in js and "/api/export" not in js
