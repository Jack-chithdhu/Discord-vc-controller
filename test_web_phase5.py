"""
Phase 5 regression checks (accessibility and failure states). No database needed.

Each test pins a defect found while validating the new UI in a real browser:
  * a page that needs the password waited on "Checking access…" forever when the
    status call could not reach the server;
  * the top bar overflowed at 320px wide;
  * the design page's loading sample had an aria-label on a role-less div.
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))


def read(*p):
    with open(os.path.join(HERE, *p), encoding="utf-8") as f:
        return f.read()


ACCESS, SHELL, COMMON = (read("static", "js", n + ".js") for n in ("access", "shell", "common"))


def test_failed_status_poll_is_announced_to_access():
    assert 'new CustomEvent("vc:status-failed"' in SHELL


def test_access_has_an_unreachable_state_that_is_never_usable():
    assert '"vc:status-failed"' in ACCESS and 'set("unreachable")' in ACCESS
    # unreachable must not count as unlocked or open, so no control becomes usable
    can_act = re.search(r"canAct:\s*function \(\) \{ return ([^}]+)\}", ACCESS).group(1)
    assert "unreachable" not in can_act
    # and a later good reading must move the page on from it
    assert 'state !== "unreachable"' in ACCESS


def test_gated_pages_explain_instead_of_waiting_forever():
    assert 'st === "unreachable"' in COMMON
    assert "Cannot reach the server" in COMMON


def test_topbar_fits_at_320px():
    css = read("static", "css", "responsive.css")
    block = re.search(r"@media \(max-width: 400px\)\s*\{(.*?)\n\}", css, re.S)
    assert block, "missing the narrow-screen rule"
    assert ".topbar-brand-text" in block.group(1) and "display: none" in block.group(1)


def test_topbar_brand_keeps_an_accessible_name_when_its_text_is_hidden():
    base = read("templates", "base.html")
    m = re.search(r'<a class="topbar-brand"[^>]*>', base)
    assert m and 'aria-label="VC Controller' in m.group(0)
    assert 'class="topbar-brand-text"' in base


def test_design_loading_sample_has_a_role_for_its_label():
    html = read("templates", "ui", "design.html")
    assert re.search(r'role="status" aria-label="Loading sample"', html)
