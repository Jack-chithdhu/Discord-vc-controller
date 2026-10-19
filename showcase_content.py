"""
showcase_content.py — what the feature-showcase IMAGES say. Pure data, no imports
beyond feature_guide (tests read it; tools/build_showcase.py renders it; app.py
only needs the file names and captions).

MAINTENANCE
  * Edit a card here, then run  `python tools/build_showcase.py`  and commit the
    regenerated PNGs in showcase/. The bot only ships the finished PNGs.
  * A test checks that every slash command and every feature page of the guide is
    covered by some card, so adding a command without mentioning it fails the build.
  * Describe only what is implemented. Planned work does NOT belong in the images.
  * Screens: real dashboard captures live in showcase/screenshots/ (made by
    tools/capture_dashboard.py). Discord-side "mocks" are drawn illustrations and are
    labelled as such. Put a real Discord screenshot at
    showcase/screenshots/<mock key>.png and it replaces the mock automatically,
    labelled "Real screenshot".
"""
import feature_guide as fg

BRAND = "VC Controller"

# Image kinds shown in the corner badge of each image.
LABEL_REAL = "REAL SCREENSHOT"
LABEL_MOCK = "ILLUSTRATIVE MOCKUP"

INTRO_TEXT = (
    "🎮 **VC Controller — what this bot can do**\n"
    "The next few images walk through every feature, one topic per image. "
    "Dashboard pictures are real screenshots of the website; Discord pictures marked "
    "*illustrative mockup* show the layout only. Type `/help` for commands, `/features` for the interactive guide."
)

# Dashboard / website screenshots: slot -> (file in showcase/screenshots, label)
SCREENS = {
    "teams": ("dashboard_teams.png", "Teams tab"),
    "lock": ("dashboard_lock.png", "Lock tab"),
    "dashboard": ("dashboard_dashboard.png", "Dashboard tab"),
    "stats": ("dashboard_stats.png", "Stats tab"),
    "log": ("dashboard_log.png", "Log tab"),
    "settings": ("dashboard_settings.png", "Settings tab"),
}

# Illustrative Discord-style panels. Button/select labels are the ones in app.py;
# there are no names, numbers or messages in them.
MOCKS = {
    "type_choice": {
        "title": "📅 Reserve a VC",
        "lines": ["Choose what you want to book."],
        "buttons": [[("🔊 Existing VC", "grey"), ("🔒 Private VC", "grey")]],
    },
    "booking_private": {
        "title": "🔒 Reserve a Private VC",
        "lines": ["Name it, add friends, pick a time, then submit."],
        "selects": ["👥 Search & select members (you're added automatically)"],
        "buttons": [[("Name & Reason", "grey"), ("Date & time", "grey"), ("Locked", "green"), ("Submit", "blue")]],
    },
    "booking_existing": {
        "title": "🔊 Reserve an existing VC",
        "lines": ["Pick the channel, then the time."],
        "selects": ["📡 Voice Channel"],
        "buttons": [[("Date & time", "grey"), ("Members", "grey"), ("Reason", "grey"), ("Lock", "green"), ("Submit", "blue")]],
    },
    "booking_time": {
        "title": "🕐 Pick your time (IST)",
        "lines": ["15-minute steps — or type any minute."],
        "selects": ["📅 Date", "🕐 Time of day", "🟢 Start time", "🔴 End time"],
        "buttons": [[("Exact times", "grey"), ("Done", "green")]],
    },
    "approval": {
        "title": "📅 New VC reservation request",
        "lines": ["Admins see the request details here and choose."],
        "buttons": [[("Give Existing VC", "green"), ("Give Private VC", "blue"), ("Decline", "red")]],
    },
    "control_panel": {
        "title": "🎛️ Your reservation (sent by DM)",
        "lines": ["Owners manage their own booking."],
        "buttons": [[("Manage Members", "grey"), ("Unlock", "green"), ("End Reservation", "red")]],
    },
    "extend_reminder": {
        "title": "⏰ Your reservation ends soon",
        "lines": ["Sent 10 minutes before the end (wording simplified)."],
        "buttons": [[("＋ Extend 15 min", "grey"), ("＋ Extend 30 min", "grey"), ("＋ Extend 1 hour", "grey")]],
    },
    "profile_page": {
        "title": "👤 Profile page (website)",
        "lines": ["Sections on every member's page:"],
        "boxes": ["Yearly activity heatmap", "Hours you usually play", "Top VC companions", "Day-by-day timeline"],
    },
    "community": {
        "title": "🎉 Buttons you'll see",
        "lines": ["Birthday, WhatsApp and games use simple buttons."],
        "buttons": [[("Enter your birthday", "blue"), ("Join WhatsApp Squad", "green")],
                    [("Higher", "grey"), ("Lower", "grey"), ("Hold", "green"), ("Spin again", "blue")]],
    },
    "achievements_panel": {
        "title": "🏅 /achievements (only you see it)",
        "lines": ["Your badges, grouped by status:"],
        "boxes": ["✅ Unlocked — badges you have earned", "⏳ In progress — with a progress bar", "⚪ Not started yet"],
    },
    "tracking_flow": {
        "title": "How tracking works",
        "flow": ["Member joins / moves", "Session opens", "Leave or disconnect logged", "Session closes", "Stats & reports update"],
    },
}

# One card = one image. `items` are (emoji, label, short text). Keep texts short:
# they are drawn large so they stay readable on a phone.
CARDS = [
    {
        "key": "welcome", "file": "01_welcome.png", "emoji": "🎮", "title": BRAND,
        "tagline": "Everything for your server's voice channels",
        "caption": "👋 **Welcome!** Here's what's inside this guide.",
        "items": [
            ("📊", "Voice tracking", "Joins, leaves, sessions, recovery"),
            ("🕹️", "VC controls", "Teams, moves, locks, mute"),
            ("📅", "Reservations", "Book, approve, extend, clean up"),
            ("👥", "Personal stats", "Who Was With Me, reports, profile"),
            ("🏅", "Achievements", "Badges, daily streaks, optional rewards"),
            ("🌐", "Web dashboard", "Six tabs, phone-friendly"),
            ("⚙️", "Admin & health", "Toggles, Doctor Mode, recovery"),
            ("🎉", "Community", "Birthdays, WhatsApp, points games"),
        ],
        "screens": [], "mocks": [], "commands": [], "covers": ["overview"],
    },
    {
        "key": "tracking", "file": "02_voice_tracking.png", "emoji": "📊", "title": "Voice tracking",
        "tagline": "Every join, leave and move — recorded",
        "caption": "📊 **Voice tracking** — runs quietly in the background; stats, reports and the website all use it.",
        "items": [
            ("🎙️", "Joins, leaves, moves", "Logged the moment they happen"),
            ("🕵️", "Who moved or disconnected", "Shown when Discord's audit log has it"),
            ("⏱️", "Session history", "How long each person stayed where"),
            ("🛡️", "Restart recovery", "Stale sessions closed, rest rebuilt from the log"),
            ("🩺", "Repair tools (admin)", "Smart recovery · rebuild stats · inspect logs"),
        ],
        "screens": [], "mocks": ["tracking_flow"],
        "commands": ["run-smart-recovery", "reset-vc-stats", "inspect-vc-logs", "delete-vc-log", "wipe-all-vc-history"],
        "covers": ["tracking"],
    },
    {
        "key": "controls", "file": "03_vc_controls.png", "emoji": "🕹️", "title": "VC controls",
        "tagline": "Run custom games from the website",
        "caption": "🕹️ **VC controls** — on the website's **Teams** and **Lock** tabs (the site password applies if one is set).",
        "items": [
            ("🎮", "Team split", "Auto-split or place players in Team A / B"),
            ("➡️", "Move → A / Move → B", "Send everyone to their team channel"),
            ("🔁", "Rematch", "Swap the sides"),
            ("🏠", "Regroup", "Everyone back to the lobby"),
            ("🔇", "Mute · deafen · disconnect", "Per player"),
            ("🔒", "Channel lock", "Keep a group together · Unlock all"),
        ],
        "screens": ["teams"], "mocks": [], "commands": [], "covers": ["controls", "locking"],
    },
    {
        "key": "booking", "file": "04_reservations_booking.png", "emoji": "📅", "title": "Reservations: booking",
        "tagline": "Book a channel for later — times in IST",
        "caption": "📅 **Booking** — start with `/vc-reserve` or the **VC Reservations** button, then choose a type.",
        "items": [
            ("🔊", "Existing VC", "Reserve a channel you already have"),
            ("🔒", "Private temporary VC", "A hidden channel just for your group"),
            ("👥", "Member picker", "Searchable — you're added automatically"),
            ("🕐", "Flexible time", "Date, time of day, 15-minute steps"),
            ("⌨️", "Exact times", "Type any minute, like 7:20 PM"),
            ("🛑", "Clash protection", "No double-booking; suggests the next free time"),
        ],
        "screens": [], "mocks": ["booking_private", "booking_time"],
        "commands": ["vc-reserve", "vc-reservations", "cancel-reservation"],
        "covers": ["booking"],
    },
    {
        "key": "lifecycle", "file": "05_reservations_lifecycle.png", "emoji": "✅", "title": "Reservations: after you book",
        "tagline": "Approval, reminders, extensions, cleanup",
        "caption": "✅ **After booking** — admins approve, you get reminders and can extend; temporary VCs tidy themselves up.",
        "items": [
            ("✅", "Admin approval", "Give Existing VC · Give Private VC · Decline"),
            ("🎛️", "Your control panel", "Manage members · Lock / Unlock · End"),
            ("⏰", "Reminder", "10 minutes before the end"),
            ("＋", "Extend", "15 min, 30 min or 1 hour — auto-approved if free"),
            ("🧹", "Cleanup", "14-minute grace, then a private VC is recycled"),
            ("🛡️", "Safe for normal VCs", "Existing channels are never emptied"),
        ],
        "screens": [], "mocks": ["approval", "extend_reminder"],
        "commands": ["extend-reservation", "reservation-stats", "reservation-set-lock"],
        "covers": ["lifecycle"],
    },
    {
        "key": "stats", "file": "06_personal_stats.png", "emoji": "👥", "title": "Personal statistics",
        "tagline": "Your own VC history — private to you",
        "caption": "👥 **Personal stats** — these replies are only visible to you.",
        "items": [
            ("👥", "Who Was With Me?", "Shared VC time with each person"),
            ("📊", "My VC Report", "Today, this week, this month, top channel"),
            ("👤", "Profile page", "Heatmap, play hours, companions, timeline"),
            ("🧾", "Reservation stats", "Time breakdown for one reservation"),
        ],
        "screens": [], "mocks": ["profile_page"],
        "commands": ["who-was-with-me", "my-vc-report", "my-profile", "reservation-stats"],
        "covers": ["stats"],
    },
    {
        "key": "achievements", "file": "06b_achievements.png", "emoji": "🏅", "title": "Achievements & streaks",
        "tagline": "Badges for real voice time — and a daily streak",
        "caption": "🏅 **Achievements** — earn badges and keep a daily streak. Try `/achievements` and `/streak` (only you see them).",
        "items": [
            ("🏅", "8 badges", "First Connection, Seven-Day Run, Night Owl…"),
            ("🔥", "Daily streak", "A day counts at 15+ minutes in voice"),
            ("🪙", "Optional points", "One-time reward per badge, never paid twice"),
            ("🏆", "Leaderboard", "Ranked by badges unlocked"),
            ("👤", "On your profile", "Streak, badges and progress"),
            ("🔔", "Unlock messages", "Off until an admin turns them on"),
        ],
        "screens": [], "mocks": ["achievements_panel"],
        "commands": ["achievements", "streak", "achievement-leaderboard"],
        "covers": ["achievements"],
    },
    {
        "key": "dashboard", "file": "07_web_dashboard.png", "emoji": "🌐", "title": "Web dashboard",
        "tagline": "Six tabs, built for phones",
        "caption": "🌐 **Web dashboard** — Dashboard · Teams · Lock · Stats · Log · Settings.",
        "items": [
            ("📊", "Dashboard", "People in voice, channels, time today, activity"),
            ("🏆", "Stats", "Leaderboards: all time, month, week, today"),
            ("📋", "Log", "Live activity log"),
            ("🎮", "Teams · 🔒 Lock", "Controls (see the VC controls image)"),
            ("⚙️", "Settings", "Feature toggles and reservation limits"),
        ],
        "screens": ["dashboard", "stats", "log"], "mocks": [], "commands": [], "covers": ["dashboard"],
    },
    {
        "key": "admin", "file": "08_admin_diagnostics.png", "emoji": "⚙️", "title": "Admin & diagnostics",
        "tagline": "Switches, limits and health checks",
        "caption": "⚙️ **Admin tools** — switches and limits are on the website's Settings tab and in slash commands.",
        "items": [
            ("🔘", "Feature toggles", "WhatsApp · announcements · birthdays · games · reservations"),
            ("📐", "Reservation limits", "Cooldown, pending and per-day caps, expiry"),
            ("🩺", "Doctor Mode", "Health scan; temporary fixes need admin approval"),
            ("🛠️", "Smart recovery", "Repair VC sessions on demand"),
            ("📣", "Panels", "VC panel, booking panel, this guide"),
        ],
        "screens": ["settings"], "mocks": [],
        "commands": ["toggle", "reservation-settings", "set-reservation-limit", "toggle-reservation-setting",
                     "doctor-mode", "run-smart-recovery", "vc-menu", "post-reservation-panel", "admin-list"],
        "covers": ["admin"],
    },
    {
        "key": "community", "file": "09_community.png", "emoji": "🎉", "title": "Community",
        "tagline": "Birthdays, WhatsApp, announcements, games",
        "caption": "🎉 **Community** — the fun side of the bot.",
        "items": [
            ("🎂", "Birthdays", "Register yours; the bot posts wishes and nudges new members"),
            ("📱", "WhatsApp squad", "Join form → admin approves → invite sent privately"),
            ("📣", "Announcements", "Authorised people DM the bot, then pick channels"),
            ("🪙", "Points", "Daily reward, balance, leaderboard"),
            ("🎰", "Games", "Coinflip · slots · dice"),
        ],
        "screens": [], "mocks": ["community"],
        "commands": ["join-whatsapp", "register-birthday", "my-birthday", "remove-birthday", "daily", "balance",
                     "coinflip", "slots", "dice", "points-leaderboard", "whatsapp-list", "join-history",
                     "set-whatsapp-link", "birthday-list", "set-birthday-channel", "set-birthday-reminder-interval"],
        "covers": ["extras"],
    },
    {
        "key": "commands_user", "file": "10_commands.png", "emoji": "📋", "title": "Commands for everyone",
        "tagline": "Type / in any channel — or use /help",
        "caption": "📋 **Commands** — `/help` lists them with examples; `/help command:<name>` explains one; `/features` opens the interactive guide.",
        "kind": "commands", "admin": False, "categories": None,
        "items": [], "screens": [], "mocks": [], "commands": ["features", "help"], "covers": [],
    },
    {
        "key": "commands_admin_1", "file": "11_admin_commands_1.png", "emoji": "🛡️", "title": "Admin commands (1/2)",
        "tagline": "Panels and reservation setup — administrators only",
        "caption": "🛡️ **Admin commands** — hidden from normal members and refused if they try.",
        "kind": "commands", "admin": True, "categories": ["Admin · Panels", "Admin · Reservations"],
        "items": [], "screens": [], "mocks": [], "commands": [], "covers": [],
    },
    {
        "key": "commands_admin_2", "file": "12_admin_commands_2.png", "emoji": "🛡️", "title": "Admin commands (2/2)",
        "tagline": "Server, community and health tools",
        "caption": "🛡️ **Admin commands, continued.** ⚠️ `/wipe-all-vc-history` is irreversible.",
        "kind": "commands", "admin": True, "categories": ["Admin · Server", "Admin · Achievements", "Admin · Health & data"],
        "items": [], "screens": [], "mocks": [], "commands": [], "covers": [],
    },
    {
        "key": "overview", "file": "13_overview.png", "emoji": "✅", "title": "Everything at a glance",
        "tagline": "All of this is live in the bot today",
        "caption": "✅ **That's everything.** Questions? Use `/help` or `/features`.",
        "kind": "overview",
        "items": [
            ("📊", "Voice tracking", "sessions · history · recovery"),
            ("🕹️", "VC controls", "teams · moves · locks · mute"),
            ("📅", "Reservations", "existing & private VCs · approval · extend"),
            ("👥", "Personal stats", "Who Was With Me · reports · profile"),
            ("🏅", "Achievements", "badges · daily streaks · rewards"),
            ("🌐", "Web dashboard", "6 tabs"),
            ("⚙️", "Admin & health", "toggles · Doctor Mode · recovery"),
            ("🎉", "Community", "birthdays · WhatsApp · points games"),
            ("📋", "Commands", "/help · /features"),
        ],
        "screens": [], "mocks": [], "commands": [], "covers": [],
    },
]


def card_commands(card: dict) -> list:
    """Command rows for a commands card: (name, short description)."""
    if card.get("kind") != "commands":
        return []
    rows = []
    for name, category, desc, usage, admin in fg.COMMANDS:
        if admin != card["admin"]:
            continue
        if card["categories"] is not None and category not in card["categories"]:
            continue
        rows.append((name, desc))
    return rows


def all_files() -> list:
    return [c["file"] for c in CARDS]


def mentioned_commands() -> set:
    """Every command that appears (by name) on at least one image."""
    names = set()
    for card in CARDS:
        names.update(card["commands"])
        names.update(n for n, _ in card_commands(card))
    return names
