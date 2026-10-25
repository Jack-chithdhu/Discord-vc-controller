PHASE 4 - Community, Analytics, Administration, Reservations (NOT deployed, NOT live)

BACKEND CHANGE (one, approved by you in Phase 2): app.py gets +40 lines, nothing removed.
  GET /api/points-leaderboard   read-only, needs the panel password, never writes.
  - Data confirmed first: table points(user_id, balance, last_daily_claim). The Discord /points-leaderboard
    command shows the top 10 to the whole channel, ungated by the points switch. The web version needs
    the password because it lists member names beside balances.
  - ?limit=1..50 (default 10); bad values give a clean 400 (not a 500). Order: balance, then user id.
  - Names come from the live server when the bot is up, otherwise null (the page shows "Member ...1234").
  - 503 if the database is not connected. 401 without the password. POST is refused (405).
  - 8 tests were written BEFORE the endpoint and failed first.

PAGES (all use existing endpoints; each has loading, empty, error and locked states)
- Community: voice-time leaderboard (All time / month / week / today) with highlights and profile links;
  points leaderboard (shown only after Unlock); member directory loaded on request, with search.
- Analytics: period tabs; total time, sessions, busiest hour, top channel; hour-of-day bar chart with
  hover tooltip and a "View as table" alternative; channel totals with peak concurrency.
- Administration (password): the 7 feature switches and the birthday reminder interval. Every switch change
  asks first and only moves after the server confirms.
- Reservations (password): the booking-limits editor. Warns if the server timezone is not set.
  Because the server saves fields one by one, the page sends ONLY changed fields, checks them first
  (no negatives, whole numbers where the server would silently cut decimals), and re-reads the real
  values after any failure.

STILL "PLANNED" (clearly labelled on the pages, nothing fake)
- Reservation list, history, and web approvals; achievements leaderboard; server-wide daily trend.
  Each needs a new endpoint. They are NOT built. See the list of proposed endpoints in the chat message
  and approve any you want.

CHANGED / NEW FILES (only these are in this ZIP)
 changed: app.py (+40 lines), static/css/pages.css, static/js/common.js (shared confirm dialog, notice, access gate)
 changed: templates/ui/community.html, analytics.html, administration.html, reservations.html
 changed: test_web_ui.py (updated: those pages now have controls, so the old "placeholder has no buttons" checks changed
          to "pages that change settings start hidden behind the password gate")
 new:     static/js/admin.js, reservations.js, community.js, analytics.js
 new:     test_points_leaderboard_api.py (8), test_web_pages_phase4.py (20)

TEST RESULTS (local, PostgreSQL 16, fake Discord)
 full suite with database : 658 passed
 full suite without DB    : 348 passed, 310 skipped (DB tests skip by design)
 pyflakes (new test files): clean; app.py has the same 3 warnings it always had
 JavaScript syntax        : all scripts pass node --check
 Dockerfile patch targets : bot.start(DISCORD_TOKEN)) and if status == 429: each still match once
 Browser run (Chromium, real Flask app, seeded fake data): about 45 checks passed, including locked views,
   unlock, switch cancel/confirm, exact request body for a one-field reservation change, chart tooltip,
   table view, bar widths under the strict CSP, and no horizontal scroll at 390px.
 NOT verified: your real server and real data volume. The members list loads everyone at once (existing
 /api/profiles behaviour), so it loads only when you press the button. Try Admin switches on a quiet moment.

UPLOAD (GitHub web, branch main) - do NOT upload this README
 repo root          : app.py (replace), test_web_ui.py (replace), test_points_leaderboard_api.py, test_web_pages_phase4.py
 static/js/         : common.js (replace); admin.js, reservations.js, community.js, analytics.js (new)
 static/css/        : pages.css (replace)
 templates/ui/      : community.html, analytics.html, administration.html, reservations.html (all replace)
 Render may redeploy on push. "/" is unchanged unless UI_VERSION=new.
