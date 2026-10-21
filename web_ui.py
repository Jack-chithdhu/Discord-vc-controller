"""
Website redesign (Phase 2): page registry, routes, security headers and the
status payload for the new UI.

Kept out of app.py on purpose. app.py only imports this module, registers it,
and exposes a few existing values (see the small block near `app = Flask`).

Nothing here changes an existing route. `/` serves the new UI only when the
environment variable UI_VERSION is "new"; the default, and `/legacy`, always
serve the original control panel.
"""
import hashlib
import math
import os
from datetime import datetime, timezone

from flask import abort, make_response, redirect, render_template

_HERE = os.path.dirname(os.path.abspath(__file__))

# slug -> page. `nav=False` pages exist but are not in the navigation.
PAGES = [
    {"slug": "overview", "title": "Overview", "short": "Overview", "icon": "home",
     "lead": "Live health of the bot and the website.", "nav": True},
    {"slug": "voice", "title": "Voice Operations", "short": "Voice", "icon": "mic",
     "lead": "Split lobbies, move teams, lock channels and disconnect members.", "nav": True},
    {"slug": "reservations", "title": "Reservations", "short": "Reserve", "icon": "calendar",
     "lead": "Temporary voice channel bookings.", "nav": True},
    {"slug": "community", "title": "Community", "short": "Community", "icon": "users",
     "lead": "Profiles, leaderboards and member activity.", "nav": True},
    {"slug": "analytics", "title": "Analytics", "short": "Analytics", "icon": "chart",
     "lead": "Voice activity over time.", "nav": True},
    {"slug": "administration", "title": "Administration", "short": "Admin", "icon": "shield",
     "lead": "Feature switches and bot settings.", "nav": True},
    {"slug": "design", "title": "Design system", "short": "Design", "icon": "home",
     "lead": "Reference for the colours, type and components used by the new UI.", "nav": False},
]
_BY_SLUG = {p["slug"]: p for p in PAGES}
NAV = [p for p in PAGES if p["nav"]]

# Strict policy: only our own files. No inline scripts or styles, no third-party
# hosts, no framing. (Avatars from Discord's CDN will need an img-src entry when
# a later phase shows them; it is deliberately not allowed yet.)
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _asset_version():
    """Short hash of the static files, used as ?v= so browsers refetch after a deploy."""
    h = hashlib.sha1()
    root = os.path.join(_HERE, "static")
    for base, _dirs, files in sorted(os.walk(root)):
        for name in sorted(files):
            path = os.path.join(base, name)
            try:
                with open(path, "rb") as f:
                    h.update(name.encode())
                    h.update(f.read())
            except OSError:
                pass
    return h.hexdigest()[:10]


ASSET_V = _asset_version()


def ui_version():
    """'new' only when UI_VERSION says so; anything else (or unset) is the classic panel."""
    return "new" if os.environ.get("UI_VERSION", "").strip().lower() == "new" else "legacy"


def render_page(slug):
    page = _BY_SLUG.get(slug)
    if page is None:
        abort(404)
    html = render_template(f"ui/{slug}.html", page=page, pages=NAV, active=slug, asset_v=ASSET_V)
    resp = make_response(html)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def register(flask_app):
    """Adds /ui and /ui/<slug>. Safe to call once at startup."""
    @flask_app.route("/ui")
    @flask_app.route("/ui/")
    def ui_root():
        return redirect("/ui/overview", code=302)

    @flask_app.route("/ui/<slug>")
    def ui_page(slug):
        return render_page(slug)


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def build_status(*, started_at, now, db_ok, bot_ready, bot_latency, password_protected,
                 tz_configured, tz_offset_hours):
    """Pure function: the JSON for GET /api/status. No personal or server data.

    `overall` is "ok" when the database and the Discord connection are both up,
    otherwise "degraded". The endpoint itself always answers 200 (if it answers,
    the web app is up); the state is in the body.
    """
    latency_ms = round(bot_latency * 1000) if _finite(bot_latency) and bot_ready else None
    return {
        "app": "vc-controller",
        "overall": "ok" if (db_ok and bot_ready) else "degraded",
        "uptime_seconds": max(0, int(now - started_at)),
        "server_time": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "timezone_offset_hours": tz_offset_hours,
        "timezone_configured": bool(tz_configured),
        "database": {"connected": bool(db_ok)},
        "bot": {"ready": bool(bot_ready), "latency_ms": latency_ms},
        "web": {"password_protected": bool(password_protected)},
    }
