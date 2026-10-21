"""
Tests for the redesigned website foundation (Phase 2).

Part 1 needs no database: design tokens, contrast, JavaScript syntax, the
status payload builder and the template files.
Part 2 loads app.py and needs TEST_DATABASE_URL (skipped otherwise), like the
other integration tests. No Discord connection is made.
"""
import importlib.util, os, re, shutil, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import web_ui  # noqa: E402

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
PASSWORD = "ui-test-password"


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ tokens and contrast
def tokens():
    css = read("static", "css", "tokens.css")
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b", css))


def lum(hexcolour):
    r, g, b = (int(hexcolour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_the_approved_palette_is_used_exactly():
    t = tokens()
    expected = {"bg": "#0B0A14", "sidebar": "#12101E", "card": "#19152A", "raised": "#211B34",
                "purple": "#A78BFA", "cyan": "#22D3EE", "success": "#4ADE80", "warning": "#FBBF24"}
    for name, value in expected.items():
        assert t[name].upper() == value


SURFACES = ["bg", "sidebar", "card", "raised"]


@pytest.mark.parametrize("fg,minimum", [("text", 7), ("text-2", 7), ("text-3", 4.5), ("purple", 4.5),
                                        ("cyan", 4.5), ("success", 4.5), ("warning", 4.5), ("danger", 4.5)])
def test_text_and_accent_colours_meet_wcag_aa_on_every_surface(fg, minimum):
    t = tokens()
    for s in SURFACES:
        assert ratio(t[fg], t[s]) >= minimum, (fg, s, ratio(t[fg], t[s]))


@pytest.mark.parametrize("fill", ["purple", "cyan", "success"])
def test_dark_text_on_filled_buttons_meets_aa(fill):
    t = tokens()
    assert ratio(t["on-accent"], t[fill]) >= 4.5


def test_focus_ring_is_clearly_visible_on_every_surface():
    t = tokens()
    for s in SURFACES:
        assert ratio(t["cyan"], t[s]) >= 3        # WCAG non-text contrast


def test_input_border_is_visible_enough_to_see_the_control():
    """Inputs use --border-strong plus a visible focus ring; record the measured value so a
    future colour change cannot silently make fields vanish into the card."""
    t = tokens()
    assert ratio(t["border-strong"], t["card"]) >= 1.4


# ------------------------------------------------------------------ static files and templates
STATIC = ["css/tokens.css", "css/layout.css", "css/components.css", "css/responsive.css",
          "js/api.js", "js/shell.js"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("path", ["js/api.js", "js/shell.js"])
def test_javascript_is_syntactically_valid(path):
    r = subprocess.run(["node", "--check", os.path.join(HERE, "static", path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_status_polling_script_only_reads():
    js = read("static", "js", "shell.js")
    assert ".post(" not in js and "method" not in js.lower().replace("// ", "")
    assert "innerHTML" not in js and "eval(" not in js


def test_api_helper_never_stores_or_logs_the_password():
    js = read("static", "js", "api.js")
    for bad in ("localStorage", "sessionStorage", "document.cookie", "console.", "?pw=", "&pw="):
        assert bad not in js


def test_css_has_no_external_requests():
    for p in STATIC:
        if p.endswith(".css"):
            css = read("static", p)
            assert "http://" not in css and "https://" not in css and "@import" not in css


def test_css_covers_required_responsive_and_accessibility_behaviour():
    layout, resp = read("static", "css", "layout.css"), read("static", "css", "responsive.css")
    assert ":focus-visible" in layout and "prefers-reduced-motion" in layout and ".skip-link" in layout
    assert "max-width: 1023px" in resp and "max-width: 767px" in resp and ".bottomnav" in resp
    assert "[hidden]" in layout


def test_every_page_template_exists_and_extends_the_base():
    for p in web_ui.PAGES:
        html = read("templates", "ui", p["slug"] + ".html")
        assert '{% extends "base.html" %}' in html


def test_templates_use_no_inline_script_style_or_event_handlers():
    files = ["base.html"] + [f"ui/{p['slug']}.html" for p in web_ui.PAGES] + ["components/macros.html", "components/icons.html"]
    for f in files:
        html = read("templates", f)
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), f
        assert "<style" not in html, f
        assert not re.search(r"\sstyle=", html), f
        assert not re.search(r"\son[a-z]+=", html), f


# ------------------------------------------------------------------ status payload (pure)
def status(**kw):
    base = dict(started_at=1000.0, now=1090.5, db_ok=True, bot_ready=True, bot_latency=0.0421,
                password_protected=True, tz_configured=True, tz_offset_hours=5.5)
    base.update(kw)
    return web_ui.build_status(**base)


def test_status_payload_shape_and_values():
    d = status()
    assert set(d) == {"app", "overall", "uptime_seconds", "server_time", "timezone_offset_hours",
                      "timezone_configured", "database", "bot", "web"}
    assert d["overall"] == "ok" and d["uptime_seconds"] == 90
    assert d["bot"] == {"ready": True, "latency_ms": 42} and d["database"] == {"connected": True}
    assert d["web"] == {"password_protected": True} and d["server_time"].endswith("+00:00")


@pytest.mark.parametrize("kw", [{"db_ok": False}, {"bot_ready": False}, {"db_ok": False, "bot_ready": False}])
def test_status_is_degraded_when_a_dependency_is_down(kw):
    assert status(**kw)["overall"] == "degraded"


@pytest.mark.parametrize("latency", [float("nan"), float("inf"), None, "x", True])
def test_bad_latency_values_become_null_not_garbage(latency):
    assert status(bot_latency=latency)["bot"]["latency_ms"] is None


def test_latency_is_hidden_while_the_bot_is_not_ready():
    assert status(bot_ready=False, bot_latency=0.05)["bot"]["latency_ms"] is None


def test_uptime_never_goes_negative():
    assert status(started_at=2000.0, now=1000.0)["uptime_seconds"] == 0


def test_status_payload_exposes_no_secret_or_member_data():
    text = repr(status())
    for bad in ("token", "DISCORD", "postgres", PASSWORD, "guild", "member", "user"):
        assert bad.lower() not in text.lower()


def test_ui_version_defaults_to_legacy(monkeypatch):
    monkeypatch.delenv("UI_VERSION", raising=False)
    assert web_ui.ui_version() == "legacy"
    for v, want in [("new", "new"), (" NEW ", "new"), ("", "legacy"), ("beta", "legacy"), ("1", "legacy")]:
        monkeypatch.setenv("UI_VERSION", v)
        assert web_ui.ui_version() == want


def test_csp_forbids_inline_code_and_third_parties():
    csp = web_ui.CSP
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "default-src 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "http" not in csp
    assert "frame-ancestors 'none'" in csp


def test_deployment_patch_targets_in_app_py_are_intact():
    """The Dockerfile's sed patches rely on each of these matching exactly once."""
    src = read("app.py")
    assert src.count("bot.start(DISCORD_TOKEN))") == 1
    assert src.count("if status == 429:") == 1


# ------------------------------------------------------------------ routes (needs the app)
@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL or "", TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_web_ui", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    return mod


@pytest.fixture()
def client(app, monkeypatch):
    monkeypatch.setattr(app, "WEB_PASSWORD", PASSWORD)
    monkeypatch.delenv("UI_VERSION", raising=False)
    return app.app.test_client()


NAV = [p["slug"] for p in web_ui.NAV]


@needs_db
def test_default_root_and_legacy_are_both_the_original_panel(app, client):
    with app.app.test_request_context():
        original = app.render_template_string(app.INDEX_HTML, locked=True)
    assert client.get("/").get_data(as_text=True) == original
    assert client.get("/legacy").get_data(as_text=True) == original


@needs_db
def test_new_ui_is_served_at_root_only_when_switched_on_and_legacy_stays(app, client, monkeypatch):
    monkeypatch.setenv("UI_VERSION", "new")
    root = client.get("/").get_data(as_text=True)
    assert 'id="app-status"' in root and "<h1>Overview</h1>" in root
    assert "switchTab(" in client.get("/legacy").get_data(as_text=True)       # rollback path intact
    monkeypatch.setenv("UI_VERSION", "off")
    assert "switchTab(" in client.get("/").get_data(as_text=True)
    monkeypatch.delenv("UI_VERSION")
    assert "switchTab(" in client.get("/").get_data(as_text=True)


@needs_db
@pytest.mark.parametrize("slug", NAV + ["design"])
def test_each_page_renders_with_the_shell_and_accessibility_basics(client, slug):
    r = client.get(f"/ui/{slug}")
    assert r.status_code == 200 and r.content_type.startswith("text/html")
    html = r.get_data(as_text=True)
    assert '<html lang="en">' in html and 'name="viewport"' in html
    assert 'class="skip-link" href="#main"' in html and '<main class="main" id="main"' in html
    assert html.count("<h1>") == 1
    assert 'aria-label="Primary"' in html and 'aria-label="Primary, mobile"' in html
    assert 'id="app-status"' in html and 'role="status"' in html
    assert len(re.findall(r'<link rel="stylesheet"', html)) == 4
    if slug != "design":                                      # design is deliberately not in the nav
        assert html.count('aria-current="page"') == 2          # sidebar + bottom nav
        assert re.search(rf'href="/ui/{slug}" title="[^"]*" aria-current="page"', html)


@needs_db
def test_navigation_links_all_resolve_and_include_the_six_pages(client):
    html = client.get("/ui/overview").get_data(as_text=True)
    hrefs = set(re.findall(r'href="(/ui/[a-z]+)"', html))
    assert {f"/ui/{s}" for s in ("overview", "voice", "reservations", "community", "analytics", "administration")} <= hrefs
    assert "/ui/design" not in hrefs
    for h in hrefs:
        assert client.get(h).status_code == 200
    assert 'href="/legacy"' in html


@needs_db
def test_ui_pages_carry_the_strict_security_headers(client):
    r = client.get("/ui/overview")
    assert r.headers["Content-Security-Policy"] == web_ui.CSP
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer" and "no-store" in r.headers["Cache-Control"]


@needs_db
def test_no_page_leaks_secrets_or_inline_code(app, client):
    for slug in NAV + ["design"]:
        html = client.get(f"/ui/{slug}").get_data(as_text=True)
        assert PASSWORD not in html and "DISCORD_TOKEN" not in html and "postgres" not in html.lower()
        assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html


@needs_db
def test_unknown_page_is_404_and_ui_root_redirects(client):
    assert client.get("/ui/nope").status_code == 404
    r = client.get("/ui")
    assert r.status_code == 302 and r.headers["Location"].endswith("/ui/overview")


@needs_db
def test_reservations_page_labels_missing_capabilities_as_planned(client):
    html = client.get("/ui/reservations").get_data(as_text=True)
    for name in ("Reservation list", "Reservation history", "Approve or decline from the website"):
        row = html.split(name, 1)[1][:400]
        assert "badge--planned" in row
    assert "<form" not in html and "<button" not in html           # nothing that pretends to act


@needs_db
def test_placeholder_pages_contain_no_action_controls(client):
    for slug in ("voice", "reservations", "community", "analytics", "administration"):
        html = client.get(f"/ui/{slug}").get_data(as_text=True)
        assert "<button" not in html and "<form" not in html and "<input" not in html, slug


@needs_db
def test_static_assets_are_served_with_the_right_types(client):
    for p in STATIC:
        r = client.get(f"/static/{p}")
        assert r.status_code == 200, p
        assert r.content_type.split(";")[0] == ("text/css" if p.endswith(".css") else "text/javascript"), p


@needs_db
def test_status_endpoint_contract_without_a_bot(app, client):
    r = client.get("/api/status")                              # public, no password
    assert r.status_code == 200 and r.is_json and "no-store" in r.headers["Cache-Control"]
    d = r.get_json()
    assert set(d) == {"app", "overall", "uptime_seconds", "server_time", "timezone_offset_hours",
                      "timezone_configured", "database", "bot", "web"}
    assert d["database"] == {"connected": True}
    assert d["bot"] == {"ready": False, "latency_ms": None} and d["overall"] == "degraded"
    assert d["web"] == {"password_protected": True} and d["timezone_offset_hours"] == 5.5


@needs_db
def test_status_reports_a_ready_bot_and_survives_a_broken_one(app, client, monkeypatch):
    class Ok:
        latency = 0.031
        def is_ready(self): return True
    monkeypatch.setattr(app, "bot", Ok())
    d = client.get("/api/status").get_json()
    assert d["overall"] == "ok" and d["bot"] == {"ready": True, "latency_ms": 31}

    class Broken:
        @property
        def latency(self): raise RuntimeError("boom")
        def is_ready(self): raise RuntimeError("boom")
    monkeypatch.setattr(app, "bot", Broken())
    r = client.get("/api/status")
    assert r.status_code == 200 and r.get_json()["bot"]["ready"] is False


@needs_db
def test_status_reports_the_database_as_down_without_a_500(app, client, monkeypatch):
    monkeypatch.setattr(app, "is_enabled", lambda: False)
    r = client.get("/api/status")
    assert r.status_code == 200 and r.get_json()["database"] == {"connected": False}


@needs_db
def test_status_does_not_weaken_authentication_on_privileged_routes(client):
    assert client.post("/api/split", json={}).status_code == 401
    assert client.get("/api/toggles").status_code == 401
