"""
achievement_logic.py — pure rules for Member Achievements & Streaks.

Like vc_stats.py this module has NO dependency on discord.py, Flask or
psycopg2 and touches no global state: plain data in, plain data out, so every
rule can be unit tested (see test_achievement_logic.py). app.py owns the
database, the Discord commands and the HTML; this file owns the *rules*.

It reuses vc_stats.py for the session maths (local-day splitting, the night
window overlap, who-was-with-me overlap) instead of re-implementing it.

=====================================================================
QUALIFICATION POLICY  (single source of truth — keep README/docs in step)
=====================================================================
Source data: vc_sessions rows, as dicts with
    user_id, start, end (None = still open), channel_id, channel_name,
    start_is_estimated, end_is_estimated.

A session is VALID when it has a start, ends after it starts and does not
start in the future. Open sessions are valid and run "until now" (the same
convention the existing stats use). Durations always come from the
timestamps, never from a stored duration column. Negative / zero lengths are
ignored. "Blips" shorter than BLIP_SECONDS (60 s) are ignored everywhere, so
rapid join/leave cycling never earns anything.

A session is PRECISE when it is closed AND neither timestamp is estimated.
(start_is_estimated / end_is_estimated are set when the bot repaired a
session after a restart — the real time is unknown, so we never treat it as
exact.)

  1. Lifetime statistics (qualifying sessions, cumulative hours):
       valid sessions, estimated ones and open ones included.
       A "qualifying session" lasts at least the daily threshold
       (default 15 min) in total.
  2. Daily streak / qualifying days:
       PRECISE sessions only, split across local midnight, same-day time
       combined. A local date qualifies when combined time >= threshold.
       Future dates never count.
  3. Time-window achievements (Night Owl 22:00-04:00 local):
       PRECISE sessions only; real overlap with the window, so a session
       that straddles 04:00 only counts its in-window part.
  4. Social shared time:
       PRECISE sessions only, SAME channel (by channel id), real
       overlapping time; bots and the member themself never count as a
       companion; each companion counts once.

STREAK RULES
  * A qualifying local date extends the streak only once per date.
  * The date right after the last qualifying date continues the run; a gap
    ends it and the next qualifying date starts a new run at 1.
  * The streak only moves FORWARD from last_qualifying_date: a date at or
    before it that shows up late (a repaired session, say) never changes the
    recorded streak, so late data cannot silently inflate it.
  * longest_streak is never reduced.
  * The first evaluation of a member (no saved state) is computed from their
    full history; later evaluations continue from the saved state.
  * A streak whose last date is older than yesterday is shown as 0 (today
    can still extend it).
  * Calendar days use the configured local offset (TIMEZONE_OFFSET_HOURS);
    nothing here hardcodes a timezone.
"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import timedelta

import vc_stats as vs

# ---------------------------------------------------------------- tunables
DEFAULT_DAILY_MINUTES = 15
DAILY_MINUTES_BOUNDS = (5, 120)
BLIP_SECONDS = 60
SOCIAL_MIN_SECONDS = 60 * 60          # shared time needed with ONE companion
NIGHT_WINDOW = (22, 4)                # local hours [22:00, 04:00)
MAX_REWARD = 10_000
MAX_TARGET = 100_000

# metrics that need a trustworthy calendar (blocked if the timezone is unsafe)
CALENDAR_METRICS = frozenset({"longest_streak", "best_month_days", "night_hours"})


# ---------------------------------------------------------------- catalogue
@dataclass(frozen=True)
class Achievement:
    id: str                 # stable forever — never reuse or rename
    name: str               # display only, free to change
    description: str
    category: str
    metric: str             # key of the metrics dict below
    target: float
    unit: str               # label for progress text
    reward: int = 0         # one-time points, 0 = badge only
    enabled: bool = True
    order: int = 0
    emoji: str = "\U0001F3C5"


CATALOGUE = (
    Achievement("first_connection", "First Connection", "Spend a full qualifying session in voice.",
                "Getting started", "qualifying_sessions", 1, "sessions", 10, True, 10, "\U0001F44B"),
    Achievement("regular", "Regular", "Complete 10 qualifying voice sessions.",
                "Getting started", "qualifying_sessions", 10, "sessions", 25, True, 20, "\U0001F4C6"),
    Achievement("seven_day_run", "Seven-Day Run", "Be active on 7 days in a row.",
                "Streaks", "longest_streak", 7, "days", 30, True, 30, "\U0001F525"),
    Achievement("monthly_regular", "Monthly Regular", "Be active on 20 different days in one calendar month.",
                "Streaks", "best_month_days", 20, "days", 75, True, 40, "\U0001F5D3️"),
    Achievement("social_gamer", "Social Gamer", "Share at least 60 minutes of voice time with each of 5 different members.",
                "Social", "social_companions", 5, "members", 40, True, 50, "\U0001F91D"),
    Achievement("night_owl", "Night Owl", "Spend 10 hours in voice between 10 PM and 4 AM.",
                "Time", "night_hours", 10, "hours", 25, True, 60, "\U0001F989"),
    Achievement("voice_veteran", "Voice Veteran", "Reach 25 hours in voice.",
                "Milestones", "lifetime_hours", 25, "hours", 50, True, 70, "\U0001F396️"),
    Achievement("dedicated", "Dedicated", "Reach 100 hours in voice.",
                "Milestones", "lifetime_hours", 100, "hours", 100, True, 80, "\U0001F451"),
)


def catalogue_by_id(catalogue=CATALOGUE) -> dict:
    return {a.id: a for a in catalogue}


def effective_catalogue(overrides=None, catalogue=CATALOGUE) -> list:
    """The catalogue with admin overrides applied, sorted for display.
    overrides: {achievement_id: {"reward": int, "target": number, "enabled": bool}}.
    Unknown ids and out-of-range values are ignored (never raise): a bad stored
    override must not take the feature down."""
    out = []
    for a in catalogue:
        o = (overrides or {}).get(a.id) or {}
        changes = {}
        reward = o.get("reward")
        if isinstance(reward, int) and not isinstance(reward, bool) and 0 <= reward <= MAX_REWARD:
            changes["reward"] = reward
        target = o.get("target")
        if isinstance(target, (int, float)) and not isinstance(target, bool) and 0 < target <= MAX_TARGET:
            changes["target"] = target
        if isinstance(o.get("enabled"), bool):
            changes["enabled"] = o["enabled"]
        out.append(replace(a, **changes) if changes else a)
    return sorted(out, key=lambda a: (a.order, a.id))


# ---------------------------------------------------------------- settings helpers
def clamp_daily_minutes(value) -> int:
    lo, hi = DAILY_MINUTES_BOUNDS
    try:
        v = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_MINUTES
    return max(lo, min(hi, v))


def parse_toggle(raw):
    """on/off style admin input -> True / False, or None when it is not understood."""
    v = str(raw).strip().lower() if raw is not None else ""
    if v in ("on", "true", "yes", "1", "enable", "enabled"):
        return True
    if v in ("off", "false", "no", "0", "disable", "disabled"):
        return False
    return None


def parse_bool(raw, default=False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "on", "yes")


# ---------------------------------------------------------------- sessions
def _utc(t):
    return None if t is None else vs.parse_time(t)


def normalise_sessions(rows, now) -> list:
    """Turn DB-ish rows into clean session dicts, dropping anything invalid
    (no start, future start, zero/negative length). `end` stays None for open
    sessions; `eff_end` is the end used for maths (open -> now, never after now)."""
    out = []
    for r in rows or []:
        start = _utc(r.get("start"))
        if start is None or start >= now:
            continue
        end = _utc(r.get("end"))
        eff_end = now if end is None else min(end, now)
        if eff_end <= start:
            continue
        out.append({
            "user_id": str(r.get("user_id")),
            "user_name": r.get("user_name"),
            "channel_id": None if r.get("channel_id") in (None, "") else str(r.get("channel_id")),
            "channel_name": r.get("channel_name"),
            "start": start, "end": end, "eff_end": eff_end,
            "start_is_estimated": bool(r.get("start_is_estimated")),
            "end_is_estimated": bool(r.get("end_is_estimated")),
            "seconds": (eff_end - start).total_seconds(),
        })
    return out


def lifetime_sessions(sessions) -> list:
    """Policy 1: every valid session that is not a blip."""
    return [s for s in sessions if s["seconds"] >= BLIP_SECONDS]


def is_precise(s) -> bool:
    return (s["end"] is not None and not s["start_is_estimated"] and not s["end_is_estimated"]
            and s["seconds"] >= BLIP_SECONDS)


def precise_sessions(sessions) -> list:
    """Policies 2-4: closed, nothing estimated. Returned in the shape vc_stats
    expects (start/end both set)."""
    return [{**s, "start": s["start"], "end": s["eff_end"]} for s in sessions if is_precise(s)]


# ---------------------------------------------------------------- local calendar
def local_today(now, tz_hours):
    return (now + timedelta(hours=tz_hours)).date()


def qualifying_dates(precise, tz_hours, daily_minutes, today) -> list:
    """Local dates on which combined precise time reached the threshold
    (sessions are split at local midnight by vc_stats.daily_time_totals)."""
    need = clamp_daily_minutes(daily_minutes) * 60
    totals = vs.daily_time_totals(precise, tz_offset_hours=tz_hours)
    return sorted(d for d, secs in totals.items() if secs >= need and d <= today)


def month_counts(dates) -> dict:
    counts = defaultdict(int)
    for d in dates:
        counts[(d.year, d.month)] += 1
    return dict(counts)


def night_seconds(precise, tz_hours) -> float:
    total = 0.0
    for s in precise:
        shifted = vs._shift_session({"start": s["start"], "end": s["end"]}, tz_hours)
        total += vs._overlap_seconds(shifted, NIGHT_WINDOW[0], NIGHT_WINDOW[1])
    return total


# ---------------------------------------------------------------- streak engine
@dataclass(frozen=True)
class StreakState:
    current: int = 0
    longest: int = 0
    last_date: object = None   # datetime.date or None


def advance_streak(state, dates, today) -> StreakState:
    """Move the streak forward over `dates` (see STREAK RULES). Pure and
    idempotent: feeding the same dates again returns an equal state."""
    state = state or StreakState()
    usable = sorted({d for d in dates if d <= today})
    cur, longest, last = state.current, state.longest, state.last_date
    for d in usable:
        if last is not None and d <= last:
            continue                       # at/before the recorded date: never rewrite history
        cur = cur + 1 if (last is not None and d == last + timedelta(days=1)) else 1
        longest = max(longest, cur)
        last = d
    return StreakState(cur, longest, last)


def effective_current(state, today) -> int:
    """The streak as it should be shown today: a run that was not extended
    yesterday or today is over."""
    if state is None or state.last_date is None:
        return 0
    return state.current if (today - state.last_date).days <= 1 else 0


# ---------------------------------------------------------------- social
def shared_time(user_id, precise_all, bot_ids=()) -> dict:
    """{other_user_id: seconds shared in the SAME channel}, using the tested
    vc_stats.compute_who_was_with_me. Channels are matched by id (renaming a
    channel never splits or merges anything); sessions with no channel are
    skipped. Bots and the member themself are never companions."""
    user_id = str(user_id)
    bots = {str(b) for b in bot_ids}
    keyed = []
    for s in precise_all:
        key = (f"id:{s['channel_id']}" if s["channel_id"]
               else (f"name:{s['channel_name']}" if s["channel_name"] else None))
        if key is None or s["user_id"] in bots:
            continue
        keyed.append({**s, "channel_name": key})
    ranked = vs.compute_who_was_with_me(user_id, keyed, top=10 ** 9)
    return {r["user_id"]: r["seconds"] for r in ranked if r["user_id"] != user_id}


# ---------------------------------------------------------------- evaluation
@dataclass
class Evaluation:
    metrics: dict
    streak: StreakState
    progress: list = field(default_factory=list)   # one dict per catalogue entry
    calendar_ok: bool = True
    dates: list = field(default_factory=list)

    def eligible_ids(self) -> list:
        return [p["id"] for p in self.progress if p["eligible"]]


def evaluate_member(user_id, sessions, others, now, tz_hours, daily_minutes=DEFAULT_DAILY_MINUTES,
                    catalogue=None, prior_streak=None, bot_ids=(), calendar_ok=True) -> Evaluation:
    """Everything the feature needs for one member, from plain session rows.

    sessions: this member's vc_sessions rows.
    others:   other members' rows that overlap this member's channels/time
              (only used for the social metric; may be empty).
    prior_streak: saved StreakState, or None for a first evaluation.
    calendar_ok: False when the local timezone is not trustworthy; calendar
              metrics are then left unchanged and cannot unlock anything.
    Deterministic for a given input; never grants anything itself."""
    catalogue = list(catalogue) if catalogue is not None else effective_catalogue()
    user_id = str(user_id)
    mine = normalise_sessions([r for r in sessions if str(r.get("user_id")) == user_id], now)
    life = lifetime_sessions(mine)
    need = clamp_daily_minutes(daily_minutes) * 60
    qualifying = sum(1 for s in life if s["seconds"] >= need)
    lifetime_hours = sum(s["seconds"] for s in life) / 3600.0

    precise_mine = precise_sessions(mine)
    today = local_today(now, tz_hours)
    if calendar_ok:
        dates = qualifying_dates(precise_mine, tz_hours, daily_minutes, today)
        streak = advance_streak(prior_streak, dates, today)
        monthly = month_counts(dates)
        night_hours = night_seconds(precise_mine, tz_hours) / 3600.0
    else:
        dates, monthly, night_hours = [], {}, 0.0
        streak = prior_streak or StreakState()

    others_precise = precise_sessions(normalise_sessions(
        [r for r in others if str(r.get("user_id")) != user_id], now))
    companions = shared_time(user_id, precise_mine + others_precise, bot_ids)
    social = sum(1 for secs in companions.values() if secs >= SOCIAL_MIN_SECONDS)

    metrics = {
        "qualifying_sessions": qualifying,
        "lifetime_hours": round(lifetime_hours, 4),
        "longest_streak": streak.longest,
        "current_streak": effective_current(streak, today),
        "best_month_days": max(monthly.values()) if monthly else 0,
        "social_companions": social,
        "night_hours": round(night_hours, 4),
        "qualifying_dates": len(dates),
    }
    progress = []
    for a in catalogue:
        value = metrics.get(a.metric, 0)
        blocked = (not calendar_ok) and a.metric in CALENDAR_METRICS
        progress.append({
            "id": a.id, "value": value, "target": a.target,
            "fraction": 0.0 if a.target <= 0 else max(0.0, min(1.0, value / a.target)),
            "eligible": bool(a.enabled and not blocked and value >= a.target),
            "blocked": blocked,
        })
    return Evaluation(metrics=metrics, streak=streak, progress=progress, calendar_ok=calendar_ok, dates=dates)


# ---------------------------------------------------------------- presentation helpers (pure text)
def progress_bar(fraction, width=10) -> str:
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "▰" * filled + "▱" * (width - filled)


def _num(v) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if isinstance(v, float) and v != int(v) else str(int(v))


def progress_text(a: Achievement, value) -> str:
    shown = min(value, a.target)
    return f"{_num(shown)}/{_num(a.target)} {a.unit}"


def achievement_status(p: dict, unlocked: bool) -> str:
    if unlocked:
        return "unlocked"
    return "in_progress" if p["value"] > 0 else "not_started"


def achievement_lines(catalogue, progress, unlocked_ids, rewards_enabled=True) -> dict:
    """{'unlocked': [...], 'in_progress': [...], 'not_started': [...]} of display
    lines. Rewards appear only for achievements that actually have one."""
    by_id = {p["id"]: p for p in progress}
    out = {"unlocked": [], "in_progress": [], "not_started": []}
    for a in catalogue:
        if not a.enabled and a.id not in unlocked_ids:
            continue
        p = by_id.get(a.id, {"value": 0, "fraction": 0.0})
        status = achievement_status(p, a.id in unlocked_ids)
        reward = f" · +{a.reward} pts" if (a.reward and rewards_enabled and status != "unlocked") else ""
        if status == "unlocked":
            out[status].append(f"{a.emoji} **{a.name}** — {a.description}")
        else:
            out[status].append(
                f"{a.emoji} **{a.name}** — {a.description}\n"
                f"{progress_bar(p['fraction'])} {progress_text(a, p['value'])}{reward}")
    return out


def next_streak_goal(catalogue, progress, unlocked_ids):
    """The nearest not-yet-unlocked streak achievement, or None."""
    by_id = {p["id"]: p for p in progress}
    goals = [a for a in catalogue if a.metric == "longest_streak" and a.enabled and a.id not in unlocked_ids]
    if not goals:
        return None
    a = min(goals, key=lambda g: g.target)
    return a, by_id.get(a.id, {"value": 0, "fraction": 0.0})


def clip_text(text, limit):
    """Never exceed a Discord limit: cut on a line boundary where possible."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    nl = cut.rfind("\n")
    return (cut[:nl] if nl > limit // 2 else cut).rstrip() + "…"
