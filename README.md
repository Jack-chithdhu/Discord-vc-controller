# Valorant VC Control — Setup

## 1. Discord Bot Setup
1. Go to https://discord.com/developers/applications → New Application
2. Bot tab → Add Bot → copy the **Token** (this is `DISCORD_TOKEN`)
3. Enable under Privileged Gateway Intents: **Server Members Intent** (Voice States isn't a privileged intent, so there's no toggle for it — it's already enabled in the code)
4. OAuth2 → URL Generator → scopes: `bot` → permissions: `Move Members`, `View Channels`, `Connect`
5. Use the generated URL to invite the bot to your server

## 2. Get your IDs
Enable Developer Mode in Discord (Settings → Advanced), then right-click to "Copy ID" for:
- Your server → `GUILD_ID`

That's the only ID you need now — the site fetches all your voice channels live and lets you pick Lobby / Team A / Team B from dropdowns each time, so it works with however many VCs you have (4-6+).

## 3. Deploy — option A: Railway (paid, ~$5/mo, requires card)
1. Push this folder to a GitHub repo (same flow as your Telegram bot)
2. Railway → New Project → Deploy from GitHub repo
3. Add these environment variables in Railway:
   - `DISCORD_TOKEN`
   - `GUILD_ID`
   - `WEB_PASSWORD` (optional — protects the page since anyone with the link could move people)
4. Deploy. Railway gives you a public URL (or add a custom domain)

## 3. Deploy — option B: Fly.io (free tier, requires card)
Fly.io's free allowance covers this bot and doesn't sleep on idle, unlike Render. A card is required for verification even on the free tier.

1. Install the CLI: `curl -L https://fly.io/install.sh | sh` (or see fly.io/docs/flyctl for Windows)
2. Sign up / log in: `fly auth signup` or `fly auth login`
3. From inside this folder, run: `fly launch` — it detects the `Dockerfile` and `fly.toml`. When it asks "Would you like to copy its configuration to the new app?" say yes, and skip creating a Postgres/Redis database.
4. Set your secrets (these become the env vars):
   ```
   fly secrets set DISCORD_TOKEN=xxx GUILD_ID=xxx WEB_PASSWORD=xxx
   ```
5. Deploy: `fly deploy`
6. Your site is live at `https://<your-app-name>.fly.dev`

`fly.toml` is already set with `min_machines_running = 1` and `auto_stop_machines = false` so the Discord bot connection doesn't drop between visits — important since a stopped machine means the bot appears offline and can't move anyone.

If you ever push code changes, just run `fly deploy` again from this folder.

## 3. Deploy — option C: Render (free, no card needed)
Render's free tier doesn't require a card, but it spins the app down after 15 min of inactivity — the bot disconnects from Discord until someone opens the site again (adds a 20-40 sec cold-start delay).

1. Push this folder to a GitHub repo
2. render.com → sign up (no card) → **New +** → **Web Service** → connect the repo
3. Fill in:
   - **Region**: Singapore (closest to India)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
   - **Instance Type**: Free
4. Add environment variables: `DISCORD_TOKEN`, `GUILD_ID`, `WEB_PASSWORD` (optional)
5. Create Web Service — Render builds and deploys, gives you a URL like `https://your-app.onrender.com`

### Keeping it awake (recommended)
The app has a lightweight `/ping` route that returns `pong` with no Discord calls — safe for uptime monitors to hit repeatedly.

1. Sign up at uptimerobot.com (free, no card)
2. **Add New Monitor** → HTTP(s)
3. URL: `https://your-app.onrender.com/ping`
4. Monitoring interval: 5 minutes
5. Save

As long as UptimeRobot keeps pinging `/ping`, Render won't consider the app idle, so it stays warm and the Discord bot stays connected.

## 4. Use it
- Open the URL on your phone, bookmark it / add to home screen for one-tap access
- Page loads your server's voice channels automatically — pick Lobby, Team A, and Team B from the dropdowns (works with any of your 4-6 Valorant VCs, pick a different set each time)
- Everyone joins the Lobby VC → the player list loads automatically into three zones: **Unassigned**, **Team A**, **Team B**
- **Drag a player card** from Unassigned into the Team A or Team B zone to assign them (drag back to Unassigned to undo)
- The player list **auto-refreshes every 10 seconds** so new joiners show up without tapping anything — it pauses automatically while you're mid-drag so it won't yank a card out from under your finger
- Tap **Move → Team A** / **Move → Team B** to actually move whoever's in that zone
- After the round: **Regroup to Lobby**, or **Rematch** to swap whoever's currently in Team A/B channels with each other
- If you add/rename VCs later, tap **↻ Refresh channel list**

## Notes
- `move_to()` requires the bot to have the **Move Members** permission and members to already be in a voice channel
- If you want auto-created/deleted temp VCs instead of fixed ones, that's a small extension — just ask

## Security

**Set `WEB_PASSWORD`.** Without it, anyone with the link can move any member in your entire server. Pick anything memorable — this isn't meant to be bulletproof, just enough to stop randoms.

**Restrict to your Valorant channels (recommended).** By default the site lists and can move people in *any* voice channel on your server, not just your Valorant ones. To lock it down:
1. In Discord, make sure your Valorant voice channels (Val 1, Val 2, Val 3, etc.) sit under one **category** (right-click a channel → right-click the category header → Copy ID with Developer Mode on)
2. Add an environment variable `ALLOWED_CATEGORY_ID` set to that category's ID
3. Once set, the site only shows and can only move people into channels in that category — even if someone calls the API directly with a different channel ID, it's rejected server-side

Leave `ALLOWED_CATEGORY_ID` unset to keep the old behavior (all VCs visible).
