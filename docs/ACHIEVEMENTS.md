# 🏅 Member Achievements & Streaks

Members earn badges for real voice activity and keep a daily streak. Everything is stored in PostgreSQL and survives restarts. Rewards are optional, one-time and never paid twice.

Code map: rules in `achievement_logic.py` (pure, unit tested), database + Discord + website glue in `app.py` (search for "Member Achievements"), tests in `test_achievement_logic.py`, `test_achievements_integration.py`, `test_achievements_discord.py`.

## Achievements (defaults)

IDs are permanent; display names can change without affecting existing unlocks.

| ID | Name | Requirement | Reward |
|---|---|---|---|
| `first_connection` | First Connection | 1 qualifying session | 10 pts |
| `regular` | Regular | 10 qualifying sessions | 25 pts |
| `seven_day_run` | Seven-Day Run | 7 qualifying days in a row | 30 pts |
| `monthly_regular` | Monthly Regular | 20 different qualifying days in one calendar month | 75 pts |
| `social_gamer` | Social Gamer | ≥ 60 min together with each of 5 different members | 40 pts |
| `night_owl` | Night Owl | 10 hours between 22:00 and 04:00 local time | 25 pts |
| `voice_veteran` | Voice Veteran | 25 hours in voice | 50 pts |
| `dedicated` | Dedicated | 100 hours in voice | 100 pts |

To add one later: add an `Achievement(...)` to `CATALOGUE` in `achievement_logic.py` that points at an existing metric (`qualifying_sessions`, `lifetime_hours`, `longest_streak`, `best_month_days`, `social_companions`, `night_hours`), then add it to `feature_guide.py` if you want it described. A brand-new kind of metric needs one new entry in `evaluate_member`.

## What counts (qualification policy)

Source: `vc_sessions`. Durations come from the timestamps, never from the stored duration column.

- **Valid session**: has a start, ends after it starts, did not start in the future. Open sessions run "until now" (same convention as the existing stats).
- **Blips**: sessions under 60 seconds never count, so rapid join/leave cycling earns nothing.
- **Precise session**: closed and neither `start_is_estimated` nor `end_is_estimated` (those flags mean the bot repaired the session after a restart, so the real time is unknown).
- **Qualifying session** (First Connection, Regular): a valid session lasting at least the daily threshold (15 min).
- **Lifetime hours** (Voice Veteran, Dedicated): all valid sessions, estimated and open ones included.
- **Qualifying day** (streaks, Monthly Regular): precise sessions only, split at local midnight, time on the same day added together, at least the threshold (15 min). Future dates never count.
- **Night Owl**: precise sessions only, real overlap with 22:00–04:00 local (a session is never classified by its start time alone).
- **Social Gamer**: precise sessions only, same channel (matched by channel id), real overlapping time, each companion counted once. The member themself and bots are never companions. A bot is recognised from the live server member cache; the voice history itself does not record it, so an uncached account counts as human.

Local days use `TIMEZONE_OFFSET_HOURS`. If that variable is missing or invalid the bot logs a warning and **pauses** streak, Monthly Regular and Night Owl progress (it never guesses UTC); everything else keeps working.

## Streak rules

- A qualifying date extends the streak once, however many sessions that day had.
- The next date continues the run; a missed date ends it and the next qualifying date starts again at 1. `longest_streak` is never reduced.
- A run whose last date is older than yesterday is shown as 0 (today can still extend it).
- The streak only moves **forward** from the saved last qualifying date, so a late-arriving older session can never inflate it.
- The first evaluation of a member is computed from their full history; later ones continue from the saved state. Evaluating the same data again changes nothing.

Note: the profile page's existing "🔥 Streak" card (any 60 seconds of VC, ending today) is a different, older statistic and is unchanged.

## Rewards (how they are kept safe)

Unlock, reward ledger row and points credit happen in **one short database transaction**. PostgreSQL uniqueness (`member_achievements` primary key, `achievement_rewards` UNIQUE plus foreign key) decides races, so concurrent commands, the background scan, retries and restarts cannot pay twice. If any step fails nothing is kept and the next evaluation retries cleanly.

- Points go to the existing `points` balance only (added to, never overwritten). There is no separate XP wallet.
- A reward of 0 gives a badge without a ledger row.
- With rewards switched off, badges still unlock but pay nothing, and turning rewards on later does **not** pay for badges already unlocked.
- Changing a reward amount or target affects future unlocks only.

## Commands

Members: `/achievements` (private), `/streak` (private), `/achievement-leaderboard [page]` (public; ranked by badges unlocked, earlier wins ties; "pts" are achievement rewards, not wallet balances).

Admins (administrator permission, checked again at run time): `/achievement-settings`, `/achievement-inspect member` (read-only), `/achievement-reconcile [member] [restart]`. None of them can grant points or edit progress directly.

`/achievement-settings` with nothing selected shows everything. Settings: feature on/off, rewards on/off, unlock DMs on/off, announcement channel, qualifying minutes per day (5–120, default 15), and per achievement: reward points (0–10000), target, enabled.

Defaults: feature **on**, rewards **on**, notifications **off**. Settings live in `bot_settings` under keys starting `achievements_`.

## Notifications

Off by default. When on, a member gets a brief DM and/or a message in one announcement channel (no pings). They are sent after the unlock is committed and are best effort: a failed DM is logged and never affects the badge or the reward. Backfill/reconcile runs are always silent, and so is a member's first evaluation if they already had voice history before the feature launched.

## When evaluation runs

- When a member opens `/achievements` or `/streak` (at most once a minute per member).
- A background scan every 5 minutes (20 s while a backlog remains) evaluates members whose sessions closed since the last scan, 25 members per tick. The first scan only records the start time, so old history never floods.
- `/achievement-reconcile` for backfill (below).
- The profile page reads only the saved summary; it never re-aggregates history.

## Website

`/profile/<id>` has a new **Achievements** section (streak, longest streak, badges, progress). It loads from `GET /api/vcdata/<id>/achievements`. Existing profile endpoints and JSON keys are unchanged.

## Deployment checklist (Render)

1. Confirm `DATABASE_URL`, `DISCORD_TOKEN`, `GUILD_ID` and `TIMEZONE_OFFSET_HOURS` (India = 5.5) are set.
2. Deploy. The tables are created automatically at startup (`CREATE TABLE IF NOT EXISTS`; safe to repeat; nothing existing is altered). Check the log line `[db] PostgreSQL ready (... member_achievements, member_streaks, achievement_rewards)`.
3. Decide on rewards **before** the backfill: if you do not want historical activity to pay out, run `/achievement-settings setting:Points rewards on/off value:off` first (badges still unlock).
4. Run `/achievement-reconcile` repeatedly. Each run evaluates up to 10 members silently and says whether the pass is finished. It resumes where it stopped and is safe to repeat. Watch the logs for `[achievements] reconcile batch`.
5. Verify with a test member: `/achievements`, `/streak`, `/achievement-inspect`, and `/balance` (balance should have grown by exactly the reward, once).
6. Optionally turn on unlock DMs / an announcement channel.
7. Check the bot and website logs, then confirm old commands and profile pages still work.

## Rollback

Prefer configuration over deleting data: `/achievement-settings setting:Feature on/off value:off` stops all evaluation, commands answer "turned off" and the profile section hides. Existing badges and points stay untouched. The tables can be left in place (they are additive and unused when the feature is off). Never reset `points` as part of a rollback. Reverting the code is also safe: older code ignores the extra tables.

## Known limits

- Rewards are paid at unlock time only; there is no retroactive payout after enabling rewards later.
- Bots are only excluded when the server member cache knows them.
- A day's qualification needs closed, non-estimated sessions, so an hours-long session currently in progress counts towards lifetime stats immediately but towards streaks only after it ends.
- The bot only evaluates members it has seen close a session or who open the commands; run `/achievement-reconcile` after a long outage.
