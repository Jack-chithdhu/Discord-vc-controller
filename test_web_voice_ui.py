"""
Phase 3 checks for the Overview data and Voice Operations page.

Static checks need no database. The page-render checks need TEST_DATABASE_URL.
They guard the rules that matter for a page that can move and disconnect people:
only known endpoints, privileged calls always send the password header, nothing
builds HTML from server text, and the markup the script depends on exists.
"""
import importlib.util, os, re, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def read(*p):
    with open(os.path.join(HERE, *p), encoding="utf-8") as f:
        return f.read()


JS = {n: read("static", "js", n + ".js") for n in ("api", "common", "access", "shell", "overview", "voice")}

READ_ENDPOINTS = {"/api/status", "/api/channels", "/api/lobby_members", "/api/lock_status", "/api/voice_log",
                  "/api/analytics/dashboard", "/api/toggles"}
WRITE_ENDPOINTS = {"/api/split", "/api/move/", "/api/rematch", "/api/regroup", "/api/disconnect",
                   "/api/voice_toggle", "/api/lock", "/api/unlock"}


def endpoints(js):
    return set(re.findall(r'"(/api/[a-z_/\-]*)', js))


def test_scripts_only_call_endpoints_that_already_exist():
    allowed = READ_ENDPOINTS | WRITE_ENDPOINTS
    for name, js in JS.items():
        for ep in endpoints(js):
            assert ep in allowed, (name, ep)


def test_overview_script_only_reads():
    js = JS["overview"]
    assert ".post(" not in js and "auth:" not in js
    assert endpoints(js) <= READ_ENDPOINTS


def test_every_write_in_the_voice_script_goes_through_the_one_authenticated_sender():
    js = JS["voice"]
    assert js.count("api.post(") == 1 and "api.post(path, body, { auth: true })" in js
    for ep in ("/api/split", "/api/move/", "/api/rematch", "/api/regroup", "/api/disconnect", "/api/voice_toggle", "/api/lock", "/api/unlock"):
        assert re.search(r'send\("' + re.escape(ep), js), ep      # reached only via send()


def test_voice_script_asks_before_each_disruptive_action():
    js = JS["voice"]
    for fn in ("moveTeam", "swap", "regroup", "disconnect", "lockSelected", "unlock"):
        body = js.split("function " + fn + "(", 1)[1].split("\n  function ", 1)[0]
        assert "confirmAction(" in body, fn
    assert "toggle(" in js and "function toggle(" in js            # mute/deafen are reversible, no prompt


def test_no_script_builds_markup_from_text_or_evaluates_code():
    for name, js in JS.items():
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert bad not in js, (name, bad)


def test_no_script_stores_anything_but_the_tab_scoped_password_and_selection():
    for name, js in JS.items():
        assert "localStorage" not in js and "document.cookie" not in js, name
    keys = set(re.findall(r'"(vc\.[a-z.]+)"', "".join(JS.values())))
    assert keys == {"vc.panel.pw", "vc.voice.selection"}


def test_password_is_never_logged_or_placed_in_a_url():
    for name, js in JS.items():
        assert "console." not in js and "?pw=" not in js and "&pw=" not in js, name
    assert 'type="password"' in read("templates", "base.html")
    assert 'autocomplete="off"' in read("templates", "base.html")


def test_a_401_relocks_the_page_instead_of_retrying():
    assert 'err.kind === "unauthorized"' in JS["voice"] and "VC.access.expire()" in JS["voice"]


def test_access_script_trusts_only_a_server_check():
    js = JS["access"]
    assert '"/api/toggles", { auth: true }' in js            # verification is a real privileged request
    assert 'kind === "unauthorized"' in js and "set(\"locked\")" in js


def ids_in(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_voice_template_has_every_element_the_script_looks_up():
    html = read("templates", "ui", "voice.html") + read("templates", "base.html")
    wanted = set(re.findall(r'\$\("([a-z\-]+)"\)', JS["voice"]))
    assert len(wanted) >= 15
    missing = wanted - ids_in(html)
    assert not missing, missing


def test_overview_template_has_every_element_the_script_looks_up():
    html = read("templates", "ui", "overview.html")
    for i in re.findall(r'getElementById\("([a-z\-]+)"\)', JS["overview"]):
        assert i in ids_in(html), i
    bound = set(re.findall(r'bind\("([a-z\-]+)"', JS["overview"]))
    declared = set(re.findall(r'data-ov="([a-z\-]+)"', html))
    assert bound <= declared, bound - declared


def test_shared_scripts_load_before_page_scripts():
    base = read("templates", "base.html")
    order = [base.index(f"/static/js/{n}.js") for n in ("api", "common", "access", "shell")]
    assert order == sorted(order) and base.index("{% block scripts %}") > order[-1]


def test_csp_still_forbids_third_party_images_and_inline_code():
    sys.path.insert(0, HERE)
    import web_ui
    assert "img-src 'self' data:" in web_ui.CSP and "unsafe-inline" not in web_ui.CSP and "http" not in web_ui.CSP


# ---------------------------------------------------------------- rendered pages
@pytest.fixture(scope="module")
def client():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL or "", TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_voice_ui", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    return mod.app.test_client()


@needs_db
def test_voice_page_renders_its_controls_and_scripts(client):
    html = client.get("/ui/voice").get_data(as_text=True)
    for i in ("btn-split", "btn-move-a", "btn-move-b", "btn-swap", "btn-regroup", "btn-lock", "btn-unlock-all", "confirm-dialog", "unlock-dialog"):
        assert f'id="{i}"' in html
    assert "/static/js/voice.js" in html and "/static/js/overview.js" not in html
    # every action starts disabled in the markup: nothing is usable before the script checks access
    for i in ("btn-split", "btn-move-a", "btn-move-b", "btn-swap", "btn-regroup", "btn-lock", "btn-unlock-all"):
        assert re.search(rf'id="{i}" disabled', html), i


@needs_db
def test_overview_page_loads_its_script_and_no_controls_that_change_anything(client):
    html = client.get("/ui/overview").get_data(as_text=True)
    assert "/static/js/overview.js" in html and "/static/js/voice.js" not in html
    body = html.split('<main class="main"', 1)[1].split("</main>", 1)[0]
    assert body.count("<button") == 1 and 'id="ov-refresh"' in body and "<form" not in body


def test_buttons_are_re_evaluated_after_a_split_assigns_players():
    """Found in the browser run: after Random split the Move buttons stayed disabled."""
    body = JS["voice"].split("function split(", 1)[1].split("\n  function ", 1)[0]
    assert "renderPlayers();\n      syncControls();" in body
