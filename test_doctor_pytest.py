"""Doctor Mode must not raise a false CRITICAL when pytest isn't installed
(the production image only installs runtime dependencies)."""
import asyncio, importlib.util, os, sys

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def app():
    os.environ.update(DISCORD_TOKEN="x", GUILD_ID="1", DATABASE_URL=URL, TIMEZONE_OFFSET_HOURS="5.5")
    spec = importlib.util.spec_from_file_location("app_doctor_test", os.path.join(HERE, "app.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    spec.loader.exec_module(mod)
    assert mod.init_db()
    return mod


def _diagnose(app):
    return asyncio.new_event_loop().run_until_complete(app.run_doctor_diagnosis())


def test_missing_pytest_is_not_reported_as_a_failure(app, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pytest" else real(name, *a))
    calls = []
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: calls.append(a))
    issues = _diagnose(app)
    assert not calls
    assert not [i for i in issues if i["component"] == "Test Suite"]


def test_failing_suite_is_still_reported_when_pytest_exists(app, monkeypatch):
    class R:
        returncode, stdout, stderr = 1, "1 failed", ""
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: R())
    issues = _diagnose(app)
    assert [i for i in issues if i["component"] == "Test Suite" and i["severity"] == "CRITICAL"]
