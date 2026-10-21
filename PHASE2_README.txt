PHASE 2 - website design foundation (NOT deployed, NOT live)

WHAT THIS ADDS
- Design system (static/css): tokens, layout, components, responsive. Approved palette, contrast-checked.
- Shell: desktop sidebar, tablet icon rail, mobile bottom navigation. Skip link, focus ring, reduced motion.
- Six pages under /ui/...: Overview (live status), Voice, Reservations, Community, Analytics, Administration.
  The last five are honest placeholders: they say what exists in the classic panel and what is Planned.
  They contain no buttons or forms, so nothing pretends to act.
- /ui/design: reference page for the colours and components (sample content, labelled).
- GET /api/status: new, read-only, public, no personal data. Always 200; state is in the body.
- /legacy: the original control panel, unchanged. Rollback path.
- Strict Content-Security-Policy on /ui pages (no inline scripts or styles, no third-party hosts).

ROLLOUT (reversible)
- Default is unchanged: "/" still shows the classic panel.
- To preview the new UI: open /ui/overview.
- To make "/" the new UI later: set environment variable UI_VERSION=new. Remove it to roll back.
  I recommend NOT doing this yet: the new pages have no voice controls until Phase 3.

CHANGED FILES (this ZIP contains only these)
 changed: app.py            (+43 lines: import web_ui, APP_STARTED_AT, "/" honours UI_VERSION, /legacy, /api/status, register)
 new:     web_ui.py
 new:     templates/base.html
 new:     templates/components/icons.html, macros.html
 new:     templates/ui/overview.html, voice.html, reservations.html, community.html, analytics.html, administration.html, design.html
 new:     static/css/tokens.css, layout.css, components.css, responsive.css
 new:     static/js/api.js, shell.js
 new:     test_web_contract.py (79 tests, written before the change, pass on old and new app.py)
 new:     test_web_ui.py (57 tests)
 new:     docs/WEB_PRIVACY_AND_SECURITY_NOTES.md
 new:     PHASE2_README.txt (this file; do not upload)

TEST RESULTS (local, PostgreSQL 16, no Discord)
 full suite with database : 596 passed
 full suite without DB    : 306 passed, 290 skipped (DB tests skip by design)
 pyflakes on new files    : clean; app.py shows the same 3 warnings it had before
 Dockerfile patch targets : bot.start(DISCORD_TOKEN)) and if status == 429: each still match exactly once (tested)
 Browser check (Chromium) : desktop 1366, tablet 900, mobile 390; no console errors, no horizontal scroll
 NOT verified: the live bot, a real Render deploy, real phones/screen readers. Status shows "Bot offline" in my
 tests because no Discord connection exists in the test environment.

UPLOAD (GitHub web, branch main)
 1. Upload app.py (replace), web_ui.py, test_web_contract.py, test_web_ui.py to the repo root.
 2. Upload the templates/ and static/ folders with their sub-folders as in this ZIP
    (templates/base.html, templates/components/..., templates/ui/..., static/css/..., static/js/...).
 3. Upload docs/WEB_PRIVACY_AND_SECURITY_NOTES.md into docs/.
 Render may auto-deploy on push. That is safe: the default page is unchanged. Then open /ui/overview and /legacy to look.
