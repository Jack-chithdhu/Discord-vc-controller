"""
join_whatsapp_cog.py

Add-on cog for the existing Valorant voice-mover bot.
Lets members request to join the WhatsApp squad via a modal form.
Phone numbers are only ever shown in a private, owner-only log channel.
Approvals/declines are manual and never expire (timeout=None).

SETUP:
1. Drop this file into your bot's /cogs folder (or wherever your cogs live).
2. Create a private text channel in your server, visible ONLY to you
   (and the bot). Right-click the channel -> Edit Channel -> Permissions ->
   deny @everyone "View Channel", allow it for yourself + the bot role.
3. Fill in OWNER_LOG_CHANNEL_ID and WHATSAPP_LINK below.
4. In your bot's main file, load it: await bot.load_extension("cogs.join_whatsapp_cog")
5. In your on_ready (or setup_hook), re-register pending approval views so
   buttons still work after a bot restart — see `restore_pending_views()`
   below and call it once at startup.
"""

import time
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = "join_requests.db"
OWNER_LOG_CHANNEL_ID = 0  # <-- set this to your private log channel's ID
WHATSAPP_LINK = "https://chat.whatsapp.com/YOUR_INVITE_LINK"  # <-- set this

# Spam protection knobs
DENIED_COOLDOWN_SECONDS = 24 * 60 * 60      # wait 24h after a decline before retrying
MIN_ACCOUNT_AGE_SECONDS = 7 * 24 * 60 * 60  # ignore brand-new Discord accounts (raid bots)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


class JoinModal(discord.ui.Modal, title="Join WhatsApp squad"):
    name = discord.ui.TextInput(label="Name / IGN", placeholder="Chith#1234", max_length=32)
    phone = discord.ui.TextInput(label="Phone number", placeholder="+91 90000 00000", max_length=20)

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # --- spam protection ---
        account_age = time.time() - interaction.user.created_at.timestamp()
        if account_age < MIN_ACCOUNT_AGE_SECONDS:
            conn.close()
            await interaction.response.send_message(
                "Your account's too new to request this yet.", ephemeral=True
            )
            return

        cur.execute(
            "SELECT status, created_at FROM requests WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (interaction.user.id,),
        )
        row = cur.fetchone()
        if row:
            status, created_at = row
            if status == "pending":
                conn.close()
                await interaction.response.send_message(
                    "You already have a request pending review.", ephemeral=True
                )
                return
            if status == "approved":
                conn.close()
                await interaction.response.send_message(
                    "You're already approved — check your DMs for the link.", ephemeral=True
                )
                return
            if status == "denied" and time.time() - created_at < DENIED_COOLDOWN_SECONDS:
                hours_left = int((DENIED_COOLDOWN_SECONDS - (time.time() - created_at)) / 3600)
                conn.close()
                await interaction.response.send_message(
                    f"You can try again in about {hours_left}h.", ephemeral=True
                )
                return
        # --- end spam protection ---

        cur.execute(
            "INSERT INTO requests (user_id, name, phone, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
            (interaction.user.id, str(self.name), str(self.phone), time.time()),
        )
        req_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Confirm to the requester only — phone is never echoed back publicly
        await interaction.response.send_message(
            "Request sent. You'll get a DM once it's reviewed.", ephemeral=True
        )

        # Post to the private owner-only log — phone visible ONLY here
        channel = self.bot.get_channel(OWNER_LOG_CHANNEL_ID)
        if channel is None:
            return  # channel ID not configured yet

        embed = discord.Embed(title="New join request", color=discord.Color.blurple())
        embed.add_field(name="Discord", value=interaction.user.mention, inline=True)
        embed.add_field(name="Name / IGN", value=str(self.name), inline=True)
        embed.add_field(name="Phone", value=str(self.phone), inline=False)
        embed.set_footer(text=f"Request #{req_id}")
        await channel.send(embed=embed, view=ApprovalView(req_id, interaction.user.id, self.bot))


class ApprovalView(discord.ui.View):
    """Approve/decline buttons with no timeout — sit here until you act on them."""

    def __init__(self, req_id: int, user_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.req_id = req_id
        self.user_id = user_id
        self.bot = bot
        # Static, predictable custom_ids so these buttons still work after a bot restart
        self.approve.custom_id = f"join_approve_{req_id}"
        self.decline.custom_id = f"join_decline_{req_id}"

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "approved")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "denied")

    async def _resolve(self, interaction: discord.Interaction, status: str):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE requests SET status = ? WHERE id = ?", (status, self.req_id))
        conn.commit()
        conn.close()

        for child in self.children:
            child.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green() if status == "approved" else discord.Color.red()
        embed.set_footer(text=f"Request #{self.req_id} — {status}")
        await interaction.response.edit_message(embed=embed, view=self)

        member = self.bot.get_user(self.user_id) or await self.bot.fetch_user(self.user_id)
        try:
            if status == "approved":
                await member.send(f"You're approved! Join here: {WHATSAPP_LINK}")
            else:
                await member.send("Your join request wasn't approved this time.")
        except discord.Forbidden:
            pass  # user has DMs closed — nothing more we can do


class JoinWhatsApp(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()

    @app_commands.command(name="join-whatsapp", description="Request to join the WhatsApp squad")
    async def join_whatsapp(self, interaction: discord.Interaction):
        await interaction.response.send_modal(JoinModal(self.bot))


async def restore_pending_views(bot: commands.Bot):
    """
    Call this once from your bot's on_ready/setup_hook so Approve/Decline
    buttons on old pending requests keep working after a restart.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, user_id FROM requests WHERE status = 'pending'")
    rows = cur.fetchall()
    conn.close()
    for req_id, user_id in rows:
        bot.add_view(ApprovalView(req_id, user_id, bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(JoinWhatsApp(bot))
