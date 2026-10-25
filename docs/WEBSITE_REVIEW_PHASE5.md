# Website review, Phase 5 (accessibility, failure states, security, final diff)

Scope: the new UI under `/ui/*`. Everything below was run against a fake Discord connection and a
local test database. Nothing was run against your live server or real member data.

## Fixed in this phase
| # | Finding | Fix |
|---|---------|-----|
| 1 | **Stuck "Checking access…"** on Reservations, Community (points) and Administration when the server could not be reached. The page waited forever. | New `unreachable` access state. The page now says "Cannot reach the server… will retry automatically", keeps every control disabled, and recovers by itself on the next good status reading. |
| 2 | Top bar overflowed on 320px-wide phones (357px) on every page. | Brand text hidden at 400px and below; the link keeps its accessible name. |
| 3 | axe `aria-prohibited-attr`: design page loading sample had `aria-label` on a div with no role. | Added `role="status"`. |

## Accessibility (axe-core 4.10.2 plus keyboard checks)
0 violations across 7 pages x 2 viewports x locked / unlocked / dialog-open. Verified: skip link, visible focus ring,
dialog focus move / trap / Escape returns focus, arrow keys on the None/A/B radios, Cancel focused first in confirm
dialogs, reduced motion, 320px reflow without sideways scroll, every control at least 24x24 CSS px (WCAG 2.2).

## Failure states (65 browser checks)
For each page: network down, HTTP 500, empty data, server that never answers, bot disconnected, password rejected
mid-session, going offline after load. Result: every case shows a clear message or an honest empty state. No
blank screens, no stuck loading skeletons, no JavaScript errors. Offline keeps the last numbers and labels them stale.
A 401 re-locks the page and forgets the stored password. Hung requests time out with a message.

## Security and secrets
- No tokens, passwords or keys in any new file. The password is held only in `sessionStorage` (this tab) and sent only in the `X-PW` header, never in a URL.
- No `innerHTML`, `eval`, inline script, inline style or inline handler in the new UI. Server text is shown with `textContent` only.
- CSP: `default-src 'none'; script-src 'self'; style-src 'self'; ...; frame-ancestors 'none'`, plus `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. No third-party hosts.
- Path handling: `/ui/../app.py`, `/ui/%2e%2e/app.py`, `/static/../app.py`, encoded variants and unknown slugs all return 404.
- Privileged endpoints return 401 without the password (`/api/toggles`, `/api/points-leaderboard`, `/api/reservation-settings`).
- `app.py` is unchanged in this phase. Both Dockerfile patch targets still match exactly once. No Dockerfile change.

## Final diff against `main`
Phase 5 changes exactly 6 existing files (listed in PHASE5_README.txt) and adds 1 test file and this report. No endpoint, response field, status code or database schema changed.

## Existing risks I did not change (need your decision)
1. **Classic panel (`templates/index.html`) builds HTML from Discord display names with `innerHTML` and has inline handlers.** A member with a crafted display name could run script in the admin's browser. The new UI does not have this problem. Suggested fix: escape names in the classic page, or make the new UI the default.
2. Items already listed in `docs/WEB_PRIVACY_AND_SECURITY_NOTES.md` (public read endpoints expose member data, `?pw=` in URLs, plain-text password compare, no rate limit, shared password, Flask dev server in production).

## Test results
664 passed with the database, 354 passed / 310 skipped without it. pyflakes: same 3 old warnings in `app.py`. All JS passes `node --check`.
