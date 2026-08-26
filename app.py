import os
import random
import asyncio
import threading
from collections import deque
from datetime import datetime, timezone

import discord
from flask import Flask, render_template, jsonify, request

# ---------- CONFIG (set these as environment variables) ----------
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")  # optional simple lock
ALLOWED_CATEGORY_ID = os.environ.get("ALLOWED_CATEGORY_ID")  # optional: restrict VC list to one category

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = discord.Client(intents=intents)
bot_loop = None

# state (in-memory, resets on restart)
last_split = {"team_a": [], "team_b": []}
voice_log = deque(maxlen=300)  # most recent voice channel activity


def log_event(member, event, from_channel=None, to_channel=None):
    voice_log.appendleft({
        "time": datetime.now(timezone.utc).isoformat(),
        "name": member.display_name,
        "avatar": member.display_avatar.url,
        "event": event,  # "joined" | "left" | "moved"
        "from": from_channel.name if from_channel else None,
        "to": to_channel.name if to_channel else None,
    })


def member_info(m):
    voice = m.voice
    return {
        "id": str(m.id),
        "name": m.display_name,
        "avatar": m.display_avatar.url,
        "muted": bool(voice and voice.mute),
        "deafened": bool(voice and voice.deaf),
    }


def run_coro(coro):
    """Run an async discord.py coroutine from Flask's sync thread."""
    future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return future.result(timeout=15)


async def list_voice_channels():
    guild = bot.get_guild(GUILD_ID)
    channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
    if ALLOWED_CATEGORY_ID:
        cat_id = int(ALLOWED_CATEGORY_ID)
        channels = [c for c in channels if c.category_id == cat_id]
    channels.sort(key=lambda c: c.position)
    return [
        {"id": str(c.id), "name": c.name, "member_count": len(c.members)}
        for c in channels
    ]


def channel_allowed(vc):
    """If ALLOWED_CATEGORY_ID is set, only permit voice channels in that category."""
    if not ALLOWED_CATEGORY_ID:
        return True
    return vc is not None and vc.category_id == int(ALLOWED_CATEGORY_ID)


async def get_channel_members(channel_id):
    guild = bot.get_guild(GUILD_ID)
    vc = guild.get_channel(channel_id)
    if vc is None or not channel_allowed(vc):
        return None
    return list(vc.members)


async def move_members(members, channel_id):
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(channel_id)
    if not channel_allowed(channel):
        return [], [{"name": m.display_name, "reason": "Target channel not allowed"} for m in members]
    moved = []
    failed = []
    for m in members:
        try:
            await m.move_to(channel)
            moved.append(member_info(m))
        except discord.Forbidden:
            failed.append({"name": m.display_name, "reason": "Bot lacks Move Members permission"})
        except discord.HTTPException as e:
            failed.append({"name": m.display_name, "reason": str(e)})
    return moved, failed


@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user}")


@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.id != GUILD_ID:
        return
    if before.channel is None and after.channel is not None:
        log_event(member, "joined", to_channel=after.channel)
    elif before.channel is not None and after.channel is None:
        log_event(member, "left", from_channel=before.channel)
    elif before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        log_event(member, "moved", from_channel=before.channel, to_channel=after.channel)


# ---------- Flask app ----------
app = Flask(__name__)


def check_auth():
    if not WEB_PASSWORD:
        return True
    return request.args.get("pw") == WEB_PASSWORD or request.headers.get("X-PW") == WEB_PASSWORD


@app.route("/")
def index():
    return render_template("index.html", locked=bool(WEB_PASSWORD))


@app.route("/ping")
def ping():
    """Lightweight health-check for uptime monitors. No auth, no Discord calls."""
    return "pong", 200


@app.route("/api/channels")
def api_channels():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    try:
        channels = run_coro(list_voice_channels())
        return jsonify({"channels": channels})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lobby_members")
def api_lobby_members():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    lobby_id = request.args.get("lobby_id")
    if not lobby_id:
        return jsonify({"error": "lobby_id required"}), 400
    try:
        members = run_coro(get_channel_members(int(lobby_id)))
        if members is None:
            return jsonify({"error": "Lobby channel not found"}), 400
        return jsonify({"members": [member_info(m) for m in members]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/split", methods=["POST"])
def api_split():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    lobby_id = body.get("lobby_id")
    if not lobby_id:
        return jsonify({"error": "lobby_id required"}), 400
    try:
        members = run_coro(get_channel_members(int(lobby_id)))
        if members is None:
            return jsonify({"error": "Lobby channel not found"}), 400
        if len(members) < 2:
            return jsonify({"error": "Need at least 2 people in the lobby VC"}), 400
        random.shuffle(members)
        half = len(members) // 2
        team_a, team_b = members[half:], members[:half]
        last_split["team_a"] = [m.id for m in team_a]
        last_split["team_b"] = [m.id for m in team_b]
        return jsonify({
            "team_a": [member_info(m) for m in team_a],
            "team_b": [member_info(m) for m in team_b],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/move/<team>", methods=["POST"])
def api_move(team):
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    if team not in ("a", "b"):
        return jsonify({"error": "bad team"}), 400
    body = request.get_json(silent=True) or {}
    target_id = body.get("channel_id")
    member_ids = body.get("member_ids")  # optional: explicit manual selection
    if not target_id:
        return jsonify({"error": "channel_id required"}), 400
    try:
        guild = bot.get_guild(GUILD_ID)
        if member_ids is not None:
            ids = [int(i) for i in member_ids]
        else:
            ids = last_split["team_a"] if team == "a" else last_split["team_b"]
        if not ids:
            return jsonify({"error": "No players selected for this team"}), 400
        members = [guild.get_member(i) for i in ids]
        members = [m for m in members if m]
        # keep last_split in sync so Rematch/Regroup stay accurate for manual picks too
        last_split["team_a" if team == "a" else "team_b"] = ids
        moved, failed = run_coro(move_members(members, int(target_id)))
        return jsonify({"moved": moved, "failed": failed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/regroup", methods=["POST"])
def api_regroup():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    from_ids = body.get("from_channel_ids", [])
    to_id = body.get("to_channel_id")
    if not from_ids or not to_id:
        return jsonify({"error": "from_channel_ids and to_channel_id required"}), 400
    try:
        guild = bot.get_guild(GUILD_ID)
        members = []
        for cid in from_ids:
            vc = guild.get_channel(int(cid))
            if vc:
                members.extend(vc.members)
        moved, failed = run_coro(move_members(members, int(to_id)))
        return jsonify({"moved": moved, "failed": failed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rematch", methods=["POST"])
def api_rematch():
    """Swap the current occupants of Team A and Team B channels with each other."""
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    team_a_channel = body.get("team_a_channel")
    team_b_channel = body.get("team_b_channel")
    if not team_a_channel or not team_b_channel:
        return jsonify({"error": "team_a_channel and team_b_channel required"}), 400
    try:
        a_members = run_coro(get_channel_members(int(team_a_channel))) or []
        b_members = run_coro(get_channel_members(int(team_b_channel))) or []
        if not a_members and not b_members:
            return jsonify({"error": "Both team channels are empty"}), 400
        moved1, failed1 = run_coro(move_members(a_members, int(team_b_channel)))
        moved2, failed2 = run_coro(move_members(b_members, int(team_a_channel)))
        last_split["team_a"] = [m.id for m in b_members]
        last_split["team_b"] = [m.id for m in a_members]
        return jsonify({"status": "swapped", "failed": failed1 + failed2})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/voice_toggle", methods=["POST"])
def api_voice_toggle():
    """Toggle mute or deafen for one member currently in a voice channel."""
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    member_id = body.get("member_id")
    action = body.get("action")  # "mute" or "deafen"
    if not member_id or action not in ("mute", "deafen"):
        return jsonify({"error": "member_id and action ('mute'|'deafen') required"}), 400
    try:
        guild = bot.get_guild(GUILD_ID)
        member = guild.get_member(int(member_id))
        if member is None:
            return jsonify({"error": "Member not found"}), 400
        if member.voice is None:
            return jsonify({"error": "Player is not in a voice channel"}), 400

        async def toggle():
            if action == "mute":
                new_state = not member.voice.mute
                await member.edit(mute=new_state)
            else:
                new_state = not member.voice.deaf
                await member.edit(deafen=new_state)
            return new_state

        new_state = run_coro(toggle())
        return jsonify({"member_id": str(member.id), "action": action, "state": new_state})
    except discord.Forbidden:
        return jsonify({"error": "Bot lacks Mute/Deafen Members permission"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/voice_log")
def api_voice_log():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    limit = min(int(request.args.get("limit", 50)), 300)
    return jsonify({"events": list(voice_log)[:limit]})


def start_bot():
    global bot_loop
    loop = asyncio.new_event_loop()
    bot_loop = loop
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot.start(DISCORD_TOKEN))


if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
