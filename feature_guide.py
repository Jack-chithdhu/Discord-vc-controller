"""
feature_guide.py — the single source of truth for the /features guide and /help.

Pure data + pure functions: no discord.py, Flask or database imports, so the
content can be unit tested (see test_feature_guide.py). app.py only turns the
dicts returned here into Discord embeds.

MAINTENANCE — to change what the guide says:
  * Edit a page in FEATURE_PAGES, or a command in COMMANDS, or a line in PLANNED.
  * Adding a slash command to app.py? Add it to COMMANDS too. A test compares
    this file against the commands registered in app.py (names AND whether each
    one is admin-only), so the build fails if they drift apart.
  * Only describe what exists in the code. Anything not built yet belongs in
    PLANNED, which is shown on its own clearly-labelled page.
  * Keep text short: every page must fit a Discord embed and read well on a
    phone (limits are checked by the tests).
"""

# Discord limits (embed): title 256, description 4096, field name 256, field
# value 1024, 25 fields, 6000 characters in total per message. We stay well under.
MAX_FIELD_VALUE = 1000
MAX_PAGE_CHARS = 3800

GUIDE_FOOTER_HINT = "Use the buttons to browse · /help for commands"

# Optional screenshot per page: a file name inside the repo's guide_assets/ folder
# (e.g. "booking_existing.png"). None = text-only page. Nothing is shown unless the
# file exists AND the site URL is configured (see app.py), so a missing file can
# never produce a broken image.

# ---------------------------------------------------------------------------
# Feature pages (all IMPLEMENTED)
# ---------------------------------------------------------------------------

FEATURE_PAGES = [
    {
        "key": "overview",
        "emoji": "\U0001F3AE",
        "title": "VC Controller — Feature Guide",
        "summary": (
            "Everything this bot does for your voice channels, in one place.\n"
            "✅ Every page except the last describes features that are **live now**. "
            "Ideas that are **not built yet** are on the final page, clearly marked."
        ),
        "fields": [
            ("\U0001F4CA Tracking", "Every join, leave, move and disconnect is logged and turned into sessions and stats."),
            ("\U0001F579️ Controls", "Team split, moving players, mute/deafen and channel locks — on the website dashboard."),
            ("\U0001F4C5 Reservations", "Book an existing VC or a private temporary VC, with admin approval, reminders and extensions."),
            ("\U0001F465 Personal stats", "**Who Was With Me?**, your VC report and your profile page."),
            ("⚙️ Admin tools", "Feature toggles, reservation limits, health checks and log tools."),
            ("\U0001F3C5 Achievements", "Badges, daily voice streaks and optional point rewards."),
            ("\U0001F389 Extras", "WhatsApp join form, birthdays, points games and DM announcements."),
        ],
        "image": "main_panel.png",
    },
    {
        "key": "tracking",
        "emoji": "\U0001F4CA",
        "title": "Voice tracking & logs",
        "summary": "The bot quietly records voice activity so stats, reports and the dashboard are always based on real data.",
        "fields": [
            ("What is recorded", (
                "• Joins, leaves, moves and disconnects\n"
                "• Who moved or disconnected someone (when Discord's audit log shows it)\n"
                "• Sessions: how long each person stayed in each channel"
            )),
            ("Where you see it", (
                "• **Log** and **Dashboard** tabs on the website\n"
                "• `/my-vc-report`, `/who-was-with-me`, your profile page"
            )),
            ("Reliability", (
                "After a restart the bot closes sessions it can no longer vouch for and rebuilds the rest "
                "from the raw log (**Smart Recovery**). Admins can also run `/run-smart-recovery` and `/doctor-mode`."
            )),
        ],
        "image": None,
    },
    {
        "key": "controls",
        "emoji": "\U0001F579️",
        "title": "Move & team controls (website)",
        "summary": "Run custom games from the **Teams** tab of the web dashboard. These controls are on the website, not slash commands.",
        "fields": [
            ("Teams tab", (
                "• Pick a lobby channel plus Team A and Team B channels\n"
                "• Drag players into Team A, Team B or the lobby (or auto-split)\n"
                "• **Move → A / Move → B** sends everyone to their team channel"
            )),
            ("Round controls", (
                "• \U0001F501 **Rematch** swaps the sides\n"
                "• \U0001F3E0 **Regroup** brings everyone back to the lobby\n"
                "• Disconnect a player, or toggle a player's mute / deafen"
            )),
            ("Access", "If the site has a password set, moving and locking need it. Viewing does not."),
        ],
        "image": "dashboard_teams.png",
    },
    {
        "key": "locking",
        "emoji": "\U0001F512",
        "title": "Channel locking & access",
        "summary": "Two kinds of locks exist: admin channel locks from the website, and per-reservation locks.",
        "fields": [
            ("Channel lock (website → Lock tab)", (
                "Lock one or more channels: the people inside are kept to that group. "
                "They can leave and rejoin, but outsiders can't join and the group can't wander into another locked channel. "
                "Anyone who breaks the lock is disconnected (or moved back, depending on the server's setting). **Unlock all** lifts it."
            )),
            ("Reservation lock", (
                "A reservation can be **Locked** (only the chosen members can join) or **Unlocked** (anyone can). "
                "It applies only between the reservation's start and end — outside that window the channel behaves normally."
            )),
            ("Managing members", "Reservation owners add or remove members from their DM control panel. Admins can override with `/reservation-set-lock`."),
        ],
        "image": "dashboard_lock.png",
    },
    {
        "key": "booking",
        "emoji": "\U0001F4C5",
        "title": "Reservations — booking",
        "summary": "Start with `/vc-reserve` or the **VC Reservations** button on the VC panel, then choose a type. All times are **IST**.",
        "fields": [
            ("\U0001F50A Existing VC", (
                "Pick a voice channel, then **Date & time**, **Members**, an optional **Reason** and Lock/Unlock, then **Submit**."
            )),
            ("\U0001F512 Private VC", (
                "A hidden, temporary channel just for your group. Use **Name & Reason**, a searchable member picker, "
                "**Date & time** and Lock/Unlock, then **Submit**. You're added automatically."
            )),
            ("Choosing the time", (
                "Pick a date (today + 13 days), a time of day, then start and end in **15-minute steps**. "
                "Need another minute? **Exact times** accepts any time like `7:20 PM`. Bookings are same-day, up to 12 hours."
            )),
            ("Protection", (
                "The bot refuses overlaps on the same channel and tells you when it's taken or when the next slot is free. "
                "Spam limits (cooldown, pending, per-day) are set by admins."
            )),
            ("Your bookings", "`/vc-reservations` lists upcoming ones. `/cancel-reservation` cancels your own."),
        ],
        "image": "booking_existing.png",
    },
    {
        "key": "lifecycle",
        "emoji": "✅",
        "title": "Reservations — approval, extensions & cleanup",
        "summary": "What happens after you submit.",
        "fields": [
            ("Approval", (
                "Admins review each request in the approval channel: **Give Existing VC**, **Give Private VC** or **Decline**. "
                "Only administrators can use those buttons. You get a DM with the decision. Unanswered requests expire."
            )),
            ("Your control panel (DM)", "**Manage Members**, **Lock / Unlock** and **End Reservation** — for your own reservation only."),
            ("During the booking", (
                "The first person to join activates it. A private VC is set up a minute before the start "
                "(renamed, visible only to its members) and is never given to two overlapping bookings."
            )),
            ("Extensions", (
                "10 minutes before the end you get a reminder with **＋ Extend 15 min / 30 min / 1 hour**. "
                "It's approved automatically if nobody else has the channel (max 12 hours total). "
                "Owners and admins can also use `/extend-reservation`."
            )),
            ("After the end", (
                "If people are still inside you're offered an extension. A **private** VC stays open for a 14-minute grace "
                "period, then anyone left is disconnected and it returns to the pool. "
                "**Normal channels are never emptied** by a reservation ending."
            )),
        ],
        "image": "booking_approval.png",
    },
    {
        "key": "stats",
        "emoji": "\U0001F465",
        "title": "Who Was With Me & personal reports",
        "summary": "See your own VC history. These replies are private to you.",
        "fields": [
            ("\U0001F465 Who Was With Me?", "`/who-was-with-me` — how much overlapping VC time you've shared with each person, all time, highest first."),
            ("\U0001F4CA My VC Report", "`/my-vc-report` — your time today, this week and this month, your most-used channel and your session count."),
            ("\U0001F464 My profile page", (
                "`/my-profile` gives your link. The page shows a yearly activity heatmap, the hours you usually play, "
                "your top VC companions and a day-by-day timeline."
            )),
            ("Reservation stats", "`/reservation-stats` shows the time breakdown for one reservation."),
        ],
        "image": "personal_report.png",
    },
    {
        "key": "achievements",
        "emoji": "\U0001F3C5",
        "title": "Achievements & daily streaks",
        "summary": "Earn badges for real voice activity and keep a daily streak. Points rewards are optional and paid once per badge.",
        "fields": [
            ("How a day counts", (
                "A day counts when you spend **15 minutes** in voice that day (local time, all sessions added together). "
                "Quick join/leave blips are ignored, and sessions the bot had to estimate don't count towards streaks. "
                "Miss a day and the streak restarts; your longest streak is kept."
            )),
            ("The badges", (
                "\U0001F44B First Connection · \U0001F4C6 Regular (10 sessions) · \U0001F525 Seven-Day Run · "
                "\U0001F5D3\uFE0F Monthly Regular (20 days in a month) · \U0001F91D Social Gamer (60 min with 5 people) · "
                "\U0001F989 Night Owl (10 h between 10 PM and 4 AM) · \U0001F396\uFE0F Voice Veteran (25 h) · \U0001F451 Dedicated (100 h)"
            )),
            ("Rewards", "Each badge can pay a one-time points bonus (admins can change or turn this off). Badges never pay twice."),
            ("Where to see it", (
                "`/achievements` (your badges and progress) · `/streak` · `/achievement-leaderboard` · "
                "the **Achievements** section on your profile page"
            )),
            ("Admins", "`/achievement-settings` · `/achievement-inspect` · `/achievement-reconcile`. Unlock messages are off until an admin turns them on."),
        ],
        "image": None,
    },
    {
        "key": "dashboard",
        "emoji": "\U0001F310",
        "title": "Web dashboard",
        "summary": "A phone-friendly website with tabs. Ask an admin for the address, or use `/my-profile` for your own page.",
        "fields": [
            ("Tabs", (
                "\U0001F4CA **Dashboard** — people in voice, channels, locked channels, time today, recent activity\n"
                "\U0001F3AE **Teams** — split, move, rematch, regroup\n"
                "\U0001F512 **Lock** — lock / unlock channels\n"
                "\U0001F3C6 **Stats** — leaderboards (all time, month, week, today) and analytics\n"
                "\U0001F4CB **Log** — live activity log\n"
                "⚙️ **Settings** — feature toggles, birthday reminder interval, reservation limits"
            )),
            ("Profiles", "Every member has a profile page, and there is a profiles directory."),
        ],
        "image": "dashboard_overview.png",
    },
    {
        "key": "admin",
        "emoji": "⚙️",
        "title": "Admin controls & settings",
        "summary": "Commands marked admin-only are limited to server administrators.",
        "fields": [
            ("Feature switches", "`/toggle` (or the website Settings tab) turns WhatsApp, announcements, birthday nudges, points games and reservations on or off."),
            ("Reservation setup", (
                "`/set-reservation-channel`, `/set-reservation-approval-channel`, `/post-reservation-panel`, "
                "`/create-temp-vc-pool-channel` (adds a private-VC channel)"
            )),
            ("Reservation rules", "`/reservation-settings`, `/set-reservation-limit`, `/toggle-reservation-setting`, `/reservation-set-lock`"),
            ("Health & data", (
                "`/doctor-mode` health check · `/run-smart-recovery` · `/reset-vc-stats` · "
                "`/inspect-vc-logs` · `/delete-vc-log` · `/wipe-all-vc-history` (irreversible)"
            )),
            ("This guide", "`/set-feature-guide-channel` and `/post-feature-guide`."),
            ("Achievements", "`/achievement-settings` · `/achievement-inspect` · `/achievement-reconcile`"),
        ],
        "image": "admin_panel.png",
    },
    {
        "key": "extras",
        "emoji": "\U0001F389",
        "title": "Community extras",
        "summary": "Smaller features that live alongside the VC tools.",
        "fields": [
            ("\U0001F4F1 WhatsApp squad", "`/join-whatsapp` opens a form; admins approve or decline and the invite link is sent privately."),
            ("\U0001F382 Birthdays", "`/register-birthday`, `/my-birthday`, `/remove-birthday`. The bot posts wishes in the configured channel."),
            ("\U0001FA99 Points & games", "`/daily`, `/balance`, `/coinflip`, `/slots`, `/dice`, `/points-leaderboard`."),
            ("\U0001F4E3 Announcements", "Authorised announcers DM the bot and pick which channels to post to."),
        ],
        "image": None,
    },
]

# Not built yet. Shown on the last page and clearly labelled — keep this honest.
PLANNED = [
    ("Monthly VC report", "A downloadable Excel report built from the existing stats."),
    ("Admin health panel", "A dedicated panel extending Doctor Mode."),
    ("Quiet automatic recovery", "Periodic background checks that repair VC sessions without a report."),
    ("Stats by channel ID", "Group channel statistics by ID so renaming a channel doesn't split its history."),
]

# ---------------------------------------------------------------------------
# Commands. admin=True means the command is registered admin-only in app.py.
# ---------------------------------------------------------------------------

COMMANDS = [
    # name, category, description, usage example, admin
    ("features", "Help", "Browse this feature guide.", "/features", False),
    ("help", "Help", "List commands, or explain one.", "/help   or   /help command:vc-reserve", False),
    ("vc-reserve", "Reservations", "Book an existing VC or a private temporary VC.", "/vc-reserve", False),
    ("vc-reservations", "Reservations", "See upcoming reservations.", "/vc-reservations", False),
    ("cancel-reservation", "Reservations", "Cancel one of your own reservations.", "/cancel-reservation", False),
    ("extend-reservation", "Reservations", "Extend a reservation (owner or admin).", "/extend-reservation reservation_id:12 minutes:30 minutes", False),
    ("reservation-stats", "Reservations", "Time breakdown for one reservation.", "/reservation-stats reservation_id:12", False),
    ("who-was-with-me", "Stats", "Your shared VC time with others.", "/who-was-with-me", False),
    ("my-vc-report", "Stats", "Your personal VC stats.", "/my-vc-report", False),
    ("my-profile", "Stats", "Link to your profile page.", "/my-profile", False),
    ("achievements", "Stats", "Your badges and progress.", "/achievements", False),
    ("streak", "Stats", "Your daily voice streak.", "/streak", False),
    ("achievement-leaderboard", "Stats", "Who has the most badges.", "/achievement-leaderboard page:2", False),
    ("join-whatsapp", "Community", "Request to join the WhatsApp squad.", "/join-whatsapp", False),
    ("register-birthday", "Community", "Register or update your birthday.", "/register-birthday", False),
    ("my-birthday", "Community", "Check the birthday on file.", "/my-birthday", False),
    ("remove-birthday", "Community", "Remove your birthday.", "/remove-birthday", False),
    ("daily", "Points", "Claim your daily points.", "/daily", False),
    ("balance", "Points", "Check your points.", "/balance", False),
    ("coinflip", "Points", "Challenge someone to a coinflip.", "/coinflip amount:50", False),
    ("slots", "Points", "Spin the slots.", "/slots amount:50", False),
    ("dice", "Points", "Guess higher or lower.", "/dice amount:50", False),
    ("points-leaderboard", "Points", "Who has the most points.", "/points-leaderboard", False),
    # ---- admin ----
    ("vc-menu", "Admin · Panels", "Post the VC control panel.", "/vc-menu", True),
    ("post-reservation-panel", "Admin · Panels", "Post the booking panel now.", "/post-reservation-panel", True),
    ("set-feature-guide-channel", "Admin · Panels", "Choose where the feature guide is posted.", "/set-feature-guide-channel channel:#guide", True),
    ("post-feature-guide", "Admin · Panels", "Post the image guide, or refresh it in place.", "/post-feature-guide   or   /post-feature-guide feature:Voice tracking mode:Refresh", True),
    ("set-reservation-channel", "Admin · Reservations", "Where members book.", "/set-reservation-channel channel:#book-a-vc", True),
    ("set-reservation-approval-channel", "Admin · Reservations", "Where admins review requests.", "/set-reservation-approval-channel channel:#approvals", True),
    ("create-temp-vc-pool-channel", "Admin · Reservations", "Add a hidden private-VC channel.", "/create-temp-vc-pool-channel category:Voice", True),
    ("reservation-settings", "Admin · Reservations", "View reservation limits.", "/reservation-settings", True),
    ("set-reservation-limit", "Admin · Reservations", "Change a reservation limit.", "/set-reservation-limit setting:max_per_day value:3", True),
    ("toggle-reservation-setting", "Admin · Reservations", "Turn a reservation rule on or off.", "/toggle-reservation-setting setting:spam_protection_enabled enabled:True", True),
    ("reservation-set-lock", "Admin · Reservations", "Override a reservation's lock.", "/reservation-set-lock reservation_id:12 state:unlocked", True),
    ("toggle", "Admin · Server", "Turn a bot feature on or off.", "/toggle", True),
    ("admin-list", "Admin · Server", "List the server's administrators.", "/admin-list", True),
    ("whatsapp-list", "Admin · Server", "Who filled the WhatsApp form.", "/whatsapp-list", True),
    ("join-history", "Admin · Server", "Recent WhatsApp approvals/declines.", "/join-history", True),
    ("set-whatsapp-link", "Admin · Server", "Update the WhatsApp invite link.", "/set-whatsapp-link new_link:<invite link>", True),
    ("birthday-list", "Admin · Server", "Everyone with a birthday on file.", "/birthday-list", True),
    ("set-birthday-channel", "Admin · Server", "Where birthday wishes go.", "/set-birthday-channel channel:#birthdays", True),
    ("set-birthday-reminder-interval", "Admin · Server", "How often to re-nudge.", "/set-birthday-reminder-interval value:7 unit:days", True),
    ("achievement-settings", "Admin · Achievements", "View or change achievement settings.", "/achievement-settings setting:Qualifying minutes per day value:20", True),
    ("achievement-inspect", "Admin · Achievements", "See a member's saved achievements (read-only).", "/achievement-inspect member:@name", True),
    ("achievement-reconcile", "Admin · Achievements", "Re-check progress from voice history.", "/achievement-reconcile member:@name", True),
    ("doctor-mode", "Admin · Health & data", "Run a VC system health check.", "/doctor-mode", True),
    ("run-smart-recovery", "Admin · Health & data", "Repair VC sessions now.", "/run-smart-recovery", True),
    ("reset-vc-stats", "Admin · Health & data", "Rebuild session stats from raw logs.", "/reset-vc-stats", True),
    ("inspect-vc-logs", "Admin · Health & data", "Look at a member's raw log rows.", "/inspect-vc-logs member:@name limit:30", True),
    ("delete-vc-log", "Admin · Health & data", "Delete one raw log row by ID.", "/delete-vc-log log_id:123", True),
    ("wipe-all-vc-history", "Admin · Health & data", "⚠️ Delete ALL VC history (irreversible).", "/wipe-all-vc-history", True),
]


# ---------------------------------------------------------------------------
# Building the pages
# ---------------------------------------------------------------------------

def _command_line(entry) -> str:
    name, _, desc, usage, _ = entry
    return f"`/{name}` — {desc}\n *e.g.* `{usage}`"


def _chunk_lines(lines: list, limit: int = MAX_FIELD_VALUE) -> list:
    """Greedy packing of whole lines into strings no longer than `limit`."""
    chunks, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _command_pages(admin: bool) -> list:
    """Command-reference pages: grouped by category, split so no page is too long."""
    entries = [c for c in COMMANDS if c[4] == admin]
    categories = []
    for entry in entries:
        if entry[1] not in categories:
            categories.append(entry[1])
    # (category, [field value chunks]) -> fields, then pack fields into pages
    fields = []
    for category in categories:
        chunks = _chunk_lines([_command_line(e) for e in entries if e[1] == category])
        for i, chunk in enumerate(chunks):
            suffix = "" if len(chunks) == 1 else f" ({i + 1}/{len(chunks)})"
            fields.append((f"{category}{suffix}", chunk))
    pages, current, size = [], [], 0
    for name, value in fields:
        if current and size + len(name) + len(value) > MAX_PAGE_CHARS:
            pages.append(current)
            current, size = [], 0
        current.append((name, value))
        size += len(name) + len(value)
    if current:
        pages.append(current)

    emoji, label = ("\U0001F6E1️", "Admin commands") if admin else ("\U0001F4CB", "Commands")
    intro = (
        "Only server administrators can use these." if admin else
        "Type `/` and pick a command. Examples show how to fill in options."
    )
    result = []
    for i, page_fields in enumerate(pages):
        suffix = "" if len(pages) == 1 else f" ({i + 1}/{len(pages)})"
        result.append({
            "key": ("admin_commands" if admin else "commands") + ("" if i == 0 else f"_{i + 1}"),
            "emoji": emoji, "title": f"{label}{suffix}", "summary": intro,
            "fields": page_fields, "image": None,
        })
    return result


def _planned_page() -> dict:
    return {
        "key": "planned",
        "emoji": "\U0001F6E0️",
        "title": "Planned — not available yet",
        "summary": "⚠️ **These are ideas on the roadmap. They do NOT exist yet** — nothing on this page can be used today.",
        "fields": [(f"⏳ {name}", desc) for name, desc in PLANNED],
        "image": None,
    }


def build_pages() -> list:
    """All guide pages, in browsing order. Indexes are stable for everyone, which
    is what lets the navigation buttons work on messages that outlive a restart."""
    return list(FEATURE_PAGES) + _command_pages(False) + _command_pages(True) + [_planned_page()]


def page_count() -> int:
    return len(build_pages())


def page_index(key: str) -> int:
    for i, page in enumerate(build_pages()):
        if page["key"] == key:
            return i
    raise KeyError(key)


def first_commands_index() -> int:
    return page_index("commands")


def page_size(page: dict) -> int:
    return len(page["title"]) + len(page["summary"]) + sum(len(n) + len(v) for n, v in page["fields"])


def render_page(index: int) -> dict:
    """Embed-ready dict for one page: title, description, fields, footer, image."""
    pages = build_pages()
    index = max(0, min(index, len(pages) - 1))
    page = pages[index]
    return {
        "title": f"{page['emoji']} {page['title']}",
        "description": page["summary"],
        "fields": list(page["fields"]),
        "footer": f"Page {index + 1}/{len(pages)} · {GUIDE_FOOTER_HINT}",
        "image": page.get("image"),
        "key": page["key"],
        "index": index,
        "total": len(pages),
    }


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def parse_footer_index(footer_text) -> int:
    """Current page from the footer text 'Page 3/13 · ...'; 0 if it can't be read."""
    import re
    match = re.match(r"\s*Page\s+(\d+)\s*/\s*(\d+)", footer_text or "")
    if not match:
        return 0
    return max(0, min(int(match.group(1)) - 1, page_count() - 1))


def navigate(current: int, action: str) -> int:
    """Target page for a navigation action: prev / next (clamped, no wrap-around),
    commands (first command page), home (overview), or jump:<key>."""
    total = page_count()
    if action == "prev":
        return max(0, current - 1)
    if action == "next":
        return min(total - 1, current + 1)
    if action == "commands":
        return first_commands_index()
    if action == "home":
        return 0
    if action.startswith("jump:"):
        return page_index(action.split(":", 1)[1])
    return max(0, min(current, total - 1))


def jump_options() -> list:
    """(label, key) for the 'Jump to' dropdown: the feature pages, the command
    references and the planned page (Discord allows 25 options; this is ~13)."""
    options = [(f"{p['emoji']} {p['title']}"[:100], p["key"]) for p in FEATURE_PAGES]
    options.append(("\U0001F4CB Commands", "commands"))
    options.append(("\U0001F6E1️ Admin commands", "admin_commands"))
    options.append(("\U0001F6E0️ Planned — not available yet", "planned"))
    return options


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

def command_names() -> list:
    return [c[0] for c in COMMANDS]


def find_command(name: str):
    name = (name or "").strip().lstrip("/").lower()
    for entry in COMMANDS:
        if entry[0] == name:
            return entry
    return None


def help_detail(name: str):
    """Embed-ready dict explaining one command, or None if unknown."""
    entry = find_command(name)
    if entry is None:
        return None
    cname, category, desc, usage, admin = entry
    return {
        "title": f"/{cname}",
        "description": desc,
        "fields": [
            ("How to use", f"`{usage}`"),
            ("Who can use it", "Server administrators only" if admin else "Everyone"),
            ("Category", category),
        ],
    }


def help_overview(is_admin: bool) -> dict:
    """The /help reply: a compact list of the commands the person can use, with
    examples. Admin commands are listed only for administrators."""
    entries = [c for c in COMMANDS if is_admin or not c[4]]
    categories = []
    for entry in entries:
        if entry[1] not in categories:
            categories.append(entry[1])
    fields = []
    for category in categories:
        chunks = _chunk_lines([_command_line(e) for e in entries if e[1] == category])
        for i, chunk in enumerate(chunks):
            fields.append((category if len(chunks) == 1 else f"{category} ({i + 1}/{len(chunks)})", chunk))
    hidden = sum(1 for c in COMMANDS if c[4])
    description = "Type `/` to use a command. `/help command:<name>` explains one in detail, `/features` shows the full guide."
    if not is_admin:
        description += f"\n*{hidden} admin-only commands are hidden from this list.*"
    return {"title": "\U0001F4D6 Help — commands", "description": description, "fields": fields}


def split_for_discord(embed_dict: dict, max_chars: int = MAX_PAGE_CHARS) -> list:
    """Splits a help/overview dict into several dicts so each stays under the size
    limit (used for the long /help list)."""
    parts, current, size = [], [], 0
    for name, value in embed_dict["fields"]:
        if current and size + len(name) + len(value) > max_chars:
            parts.append(current)
            current, size = [], 0
        current.append((name, value))
        size += len(name) + len(value)
    if current:
        parts.append(current)
    out = []
    for i, fields in enumerate(parts or [[]]):
        out.append({
            "title": embed_dict["title"] + ("" if len(parts) <= 1 else f" ({i + 1}/{len(parts)})"),
            "description": embed_dict["description"] if i == 0 else "",
            "fields": fields,
        })
    return out


def asset_names() -> set:
    """Every screenshot file name referenced by the catalog (the only files the
    site will serve from guide_assets/)."""
    return {p["image"] for p in FEATURE_PAGES if p.get("image")}
