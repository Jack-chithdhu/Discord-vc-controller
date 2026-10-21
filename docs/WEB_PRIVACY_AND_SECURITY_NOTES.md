# Website privacy and security notes

Written during the website redesign (Phase 2). These are **existing** behaviours, found in the audit. Phase 2 changes none of them, as agreed. Each item has a recommendation for you to approve separately.

| # | Existing behaviour | Risk | Recommendation |
|---|---|---|---|
| 1 | Read endpoints need no password: `/api/lobby_members`, `/api/profiles`, `/api/vcdata/<id>/*`, `/api/voice_log`, `/api/leaderboard`, `/api/analytics/dashboard`, `/profiles`, `/profile/<id>` | Anyone with the URL can see member names, IDs, avatars and who is in which voice channel | Require the password (or a separate read password) on the ones that expose members; keep `/ping` and `/api/status` public |
| 2 | Password compared with `==` | Timing attacks are theoretical here but easy to remove | Use `hmac.compare_digest` |
| 3 | Password accepted as `?pw=` | Query strings end up in server and proxy logs | Accept the `X-PW` header only; the new UI already uses the header |
| 4 | No rate limit on password attempts | Unlimited guessing | Add a small per-IP limit on 401 responses |
| 5 | One shared password, no accounts, no audit log | Cannot tell who changed a setting | Later: named admins and an audit table (needs a schema change, so separate approval) |
| 6 | No security headers on the classic pages | Clickjacking, sniffing | Add `X-Content-Type-Options`, `frame-ancestors` (the new UI pages already send them) |
| 7 | Flask development server runs in production (`app.run`) | Not hardened, single process | Run under gunicorn; needs care because the bot runs in a thread of the same process |
| 8 | `/api/voice_log?limit=abc` returns an HTML 500 | Noisy error, no data leak | Return 400 JSON |
| 9 | `/api/status` (new, public) shows bot/database up or down, uptime and whether a password is set | Tells a visitor if the panel is unprotected | Deliberate for now so the status pill works; can be reduced to up/down only if you prefer |

Nothing in Phase 2 weakens authentication on any privileged route. `test_web_contract.py` pins that for all 15 of them.
