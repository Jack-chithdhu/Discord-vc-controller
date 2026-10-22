PHASE 3 - Overview data + Voice Operations (NOT deployed, NOT live)

app.py and web_ui.py are NOT changed in this phase. No backend change, no new endpoint, no database change.
The new pages call only endpoints that already exist.

WHAT YOU GET
- Unlock: an "Unlock" button in the top bar (only shown if the server has a panel password).
  The password is checked by the server (a real request), kept only in this browser tab (sessionStorage),
  sent only in the X-PW header (never in a web address), and forgotten when the tab closes or you press "Lock panel".
  A wrong or expired password locks the page again.
- Overview: live counts (people in voice, voice channels, locked channels, voice time today), channel list,
  recent activity. Each block has its own loading, error and "showing last data" state.
- Voice Operations (/ui/voice):
  * Channels and players are visible without a password (same as the old panel).
  * With the password: Random split, Move Team A / B, Swap sides, Regroup to lobby, Mute / Deafen, Disconnect,
    Lock selected, Unlock one, Unlock all.
  * Every move, swap, regroup, disconnect, lock and unlock asks first and names who and where. Cancel is focused first.
  * Buttons are off until the action can really run; while a request is running every action is off (no double send).
  * "Done" appears only after the server says so; partly failed moves list who failed and why.
  * Players are chosen with None / A / B radio buttons (keyboard and screen-reader friendly), not drag and drop.
  * Avatars are initials, not Discord pictures, so the strict Content-Security-Policy is unchanged.
- The old panel still works at /legacy and at / (unless you set UI_VERSION=new).

SEPARATE DECISION I MADE FOR YOU
- You did not choose how to remember the password, so I used the recommended option: remembered for the tab only.
  Say "ask every page" if you want it asked on every page instead.

CHANGED FILES (only these are in this ZIP)
 changed: static/js/api.js, static/js/shell.js, static/css/responsive.css
 changed: templates/base.html, templates/ui/overview.html, templates/ui/voice.html
 changed: test_web_ui.py   (updated for the new shared scripts/styles and the voice page now having controls)
 new:     static/js/access.js, common.js, overview.js, voice.js
 new:     static/css/pages.css
 new:     test_voice_api_shapes.py (10 tests: what each voice route returns when it works, using a fake Discord)
 new:     test_web_voice_ui.py (16 tests: only known endpoints, every write authenticated, confirmations, no innerHTML, etc.)

TEST RESULTS (local, PostgreSQL 16, fake Discord; no real server)
 full suite with database : 626 passed
 full suite without DB    : 324 passed, 302 skipped (DB tests skip by design)
 pyflakes on test files   : clean
 JavaScript syntax        : all 11 scripts pass node --check
 Browser run (Chromium, real Flask app, fake Discord): locked view, wrong password, unlock, survive reload,
   split, move with cancel and confirm, manual team choice, disconnect, lock, unlock all, re-lock,
   stale stored password, no horizontal scroll at 390px, password never in a URL: all passed.
   (The browser logs two "401" lines: those are the intentional wrong-password checks.)
 One bug found and fixed by that run: after Random split the Move buttons stayed disabled. A test now guards it.
 NOT verified: your real Discord server. Moves, disconnects, mutes and locks were not run against real members.
 Please try them yourself with a test channel before relying on them.

LIMITS (honest)
- Client-side checks (e.g. Team A and Team B must differ) are convenience only; the server still decides.
  The server does not itself reject "Team A = Team B"; that is existing behaviour, left unchanged.
- Auto-refresh every 10 seconds on the Voice page; changes by others appear on the next refresh.

UPLOAD (GitHub web, branch main) - do NOT upload this README
 1. static/js/ : upload api.js, shell.js, access.js, common.js, overview.js, voice.js (replace the first two)
 2. static/css/ : upload responsive.css (replace) and pages.css (new)
 3. templates/ : upload base.html (replace)
 4. templates/ui/ : upload overview.html and voice.html (replace)
 5. repo root : upload test_web_ui.py (replace), test_voice_api_shapes.py, test_web_voice_ui.py
 Then open /ui/voice. Nothing changes at "/" unless UI_VERSION=new is set.
