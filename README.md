# 🎙️ Rem — Valorant VC Controller

> *"Discord's too slow when the round's already starting."*

Rem is the bot (and mobile-friendly web dashboard) that fixes the most annoying part of running Valorant customs: **moving 10 people between voice channels without anyone tabbing out of the game.** Split teams, regroup after a round, lock a channel down, run a WhatsApp allowlist, or blast an announcement to the whole server — all from your phone, mid-match.

---

## Why this exists

Every custom-game night has the same five minutes of chaos:
- "wait who's on my team"
- someone's stuck in the wrong voice channel
- the lobby leader is tabbed into the game and can't touch Discord
- three people just... vanish

Rem turns that into a couple of taps on a web page that works from a phone, without anyone leaving their client.

---

## ✨ What it actually does

### 🔀 Voice channel control
| Feature | What it does |
|---|---|
| **Split** | Randomizes the lobby into two teams and moves everyone in one shot |
| **Move `<team>`** | Manually shove a team into a specific channel |
| **Regroup** | Pulls everyone back into the lobby channel between rounds |
| **Rematch** | Re-splits and re-moves for game two, no manual cleanup |
| **Disconnect** | Kicks someone out of voice entirely |
| **Lock / Unlock** | Freezes a channel to specific members — anyone else who tries to join gets bounced (configurable: disconnect or auto-move them out) |
| **Voice log** | Rolling history of every move, split, and lock action |
| **Leaderboard** | Who's shown up the most, because bragging rights matter |
| **Analytics dashboard** | Daily/weekly activity, at a glance |

### 📲 WhatsApp squad requests
No more "what's your number" DMs. Members tap a **persistent button** in a channel of your choice → fill out a quick modal (name + number, kept private) → you get it in a locked-down review channel with three options:

- ✅ **Approve** → they get instantly DMed the invite link
- ⏸️ **Hold** → parked, no pressure, decide whenever
- ❌ **Decline** → polite auto-DM, no explanation owed

Comes with built-in spam protection (no duplicate requests, 24h cooldown after a decline, ignores brand-new throwaway accounts) and a `/set-whatsapp-link` admin command for when WhatsApp inevitably kills your invite link — updating it **automatically re-DMs everyone already approved** with the fresh one.

### 📣 DM-to-announce
DM Rem directly with some text and/or an image. It DMs you back a dropdown of every channel in the server — tick one or several — and it posts your message with an `@everyone` tag to all of them. No typing in-server, no copy-pasting into five channels by hand.

---

## 🧱 Stack

- **Python** — `discord.py` (bot) + **Flask** (dashboard API), running in one process
- **PostgreSQL** — voice logs, lock state, join requests, and runtime settings (like the WhatsApp link) all live here — nothing critical sits on local disk, so it survives redeploys
- Deployed on **Render**

---

## ⚙️ Setup

### 1. Environment variables

| Variable | Required | What it's for |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Your bot's token |
| `GUILD_ID` | ✅ | The server Rem operates in |
| `DATABASE_URL` | ✅ | Postgres connection string |
| `WEB_PASSWORD` | optional | Locks the web dashboard behind a shared password |
| `ALLOWED_CATEGORY_ID` | optional | Restricts the VC dropdown to one category |
| `LOCK_VIOLATION_ACTION` | optional | `disconnect` (default) or auto-move on lock violation |
| `TIMEZONE_OFFSET_HOURS` | optional | For readable timestamps in logs/dashboard |
| `OWNER_LOG_CHANNEL_ID` | for WhatsApp feature | Private channel where join requests + phone numbers get reviewed |
| `WHATSAPP_LINK` | for WhatsApp feature | Seed value — after first run, `/set-whatsapp-link` takes over |
| `JOIN_BUTTON_CHANNEL_ID` | for WhatsApp feature | Where the persistent "Join WhatsApp Squad" button gets posted |
| `ANNOUNCE_ADMIN_IDS` | for announce feature | Comma-separated Discord user IDs allowed to DM announcements |

### 2. Discord Developer Portal

Enable these under your bot's **Bot** tab:
- ✅ **Server Members Intent**
- ✅ **Message Content Intent** *(required for the DM-announce feature to read your messages)*

### 3. Deploy

```bash
pip install -r requirements.txt
python app.py
```

On Render: connect the repo, set the env vars above, deploy. Rem handles table creation on startup — no manual migrations.

---

## 🕹️ Commands & interactions

| Where | What |
|---|---|
| Web dashboard | Split / Move / Regroup / Rematch / Lock / Unlock / Leaderboard / Analytics |
| `/join-whatsapp` (any channel, or the persistent button) | Opens the join-request modal |
| `/set-whatsapp-link <link>` (admin only) | Updates the invite link + re-notifies approved members |
| DM Rem directly | Triggers the announcement flow (authorized users only) |

---

## 🤐 A word on privacy

Phone numbers submitted via `/join-whatsapp` are only ever posted in the private `OWNER_LOG_CHANNEL_ID` channel — never in public, never echoed back to the requester, never logged anywhere else. Only you decide who gets in.

---

*Built for a Discord server that got tired of asking "wait, whose team am I on?" mid-round.*
