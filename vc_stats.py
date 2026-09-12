"""
vc_stats.py — pure, side-effect-free VC session logic.

Deliberately has ZERO dependency on discord.py, Flask, or psycopg2, and
touches no global state. Every function here takes plain data in and
returns plain data out, so it can be unit tested (see test_vc_stats.py)
without a live database or Discord connection.

Two families of functions:

1. Session reconstruction — replay a list of raw vc_logs-style events into
   a list of sessions. Used both by the one-time backfill (over ALL
   history) and by the test suite.

2. Period math — clip sessions to a Today/Week/Month/All window in LOCAL
   time, correctly splitting a session that crosses a period boundary
   instead of counting it entirely toward whichever side it happens to
   end on (this was the Critical bug from the audit).

app.py's job is only to fetch rows from vc_sessions and call these
functions — it should not reimplement any of this math itself.
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict


# ---------- time helpers ----------

def parse_time(t):
    """Accepts a datetime or an ISO string and always returns a tz-aware UTC datetime."""
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


# ---------- 1. session reconstruction from raw events ----------

def reconstruct_sessions_with_ids(events, now=None, close_open_at_now=True):
    """
    events: list of dicts with action, user_id, user_name, from_channel_name,
            to_channel_name, created_at — MUST be ordered ASCENDING by time.
    now: current time to close any still-open sessions against (defaults to utcnow).
    close_open_at_now: if True (default — matches old behavior), any session
        still open at the end of the event list is closed at `now`. If False
        (used by the one-time backfill), sessions still open at the end are
        left open (end=None) instead — the caller is expected to reconcile
        them against real presence afterward, rather than guessing "now" is
        an accurate end time for a backfill that may run long after the fact.

    Returns a list of session dicts:
        {user_id, user_name, channel_name, start, end (None if still open), duration_seconds}

    No artificial cap on session length — a genuinely long session simply
    stays open (or reports a large duration) until a real closing event
    arrives. Distinguishing "genuinely long" from "stale because the bot
    missed the close event" is NOT this function's job — that's what live
    reconciliation against real Discord presence is for (see app.py).

    LOCK_VIOLATION events are ignored — they represent a blocked attempt,
    not an actual channel membership change.
    """
    now = now or datetime.now(timezone.utc)
    open_sessions = {}  # user_id -> {"channel_name": ..., "start": datetime, "user_name": ...}
    sessions = []

    for e in events:
        action = (e.get("action") or "").upper()
        if action == "LOCK_VIOLATION":
            continue

        uid = e["user_id"]
        uname = e.get("user_name") or "Unknown"
        t = parse_time(e["created_at"])

        if action in ("JOINED", "MOVED"):
            if uid in open_sessions:
                # Defensive: already open (missed a close event) — close it here first.
                sessions.append({**_close(open_sessions.pop(uid), t), "user_id": uid})
            open_sessions[uid] = {"channel_name": e.get("to_channel_name"), "start": t, "user_name": uname}

        elif action in ("LEFT", "DISCONNECTED"):
            if uid in open_sessions:
                sessions.append({**_close(open_sessions.pop(uid), t), "user_id": uid})
            # else: closing event with no matching open session — nothing to close.
            # (This case is exactly audit bug #2. It can still happen for events
            # that predate all logging. Live reconciliation prevents it going
            # forward by opening a session the moment we discover someone in
            # voice with no open row, so this path should get rarer over time.)

    for uid, s in open_sessions.items():
        if close_open_at_now:
            sessions.append({**_close(s, now), "user_id": uid})
        else:
            sessions.append({
                "user_id": uid, "user_name": s["user_name"], "channel_name": s["channel_name"],
                "start": s["start"], "end": None, "duration_seconds": None,
            })

    return sessions


def _close(open_session, end_time):
    start = open_session["start"]
    duration = max(0.0, (end_time - start).total_seconds())
    return {
        "user_name": open_session["user_name"],
        "channel_name": open_session["channel_name"],
        "start": start,
        "end": end_time,
        "duration_seconds": duration,
    }


# ---------- 2. period bounds & clipping ----------

def period_bounds(period, now=None, tz_offset_hours=0):
    """
    Returns (start_utc, end_utc) for the requested period, in LOCAL time
    (shifted by tz_offset_hours) then converted back to UTC bounds.
    end_utc is exclusive. For "all", returns (None, None) — no bound at all,
    meaning literally every session in the table, not a rolling window.
    """
    now = now or datetime.now(timezone.utc)
    if period == "all":
        return None, None

    local_now = now + timedelta(hours=tz_offset_hours)

    if period == "today":
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
    elif period == "yesterday":
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        local_end = local_start + timedelta(days=1)
    elif period == "week":
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_start = local_start - timedelta(days=local_start.weekday())  # Monday
        local_end = local_start + timedelta(days=7)
    elif period == "month":
        local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if local_start.month == 12:
            local_end = local_start.replace(year=local_start.year + 1, month=1)
        else:
            local_end = local_start.replace(month=local_start.month + 1)
    else:
        return None, None

    utc_start = local_start - timedelta(hours=tz_offset_hours)
    utc_end = local_end - timedelta(hours=tz_offset_hours)
    return utc_start, utc_end


def clip_sessions_to_period(sessions, period_start, period_end, now=None):
    """
    THE core fix for the Critical period-filtering bug. Takes sessions with
    possibly-open ends (end=None means still ongoing) and:

      1. Treats a still-open session's end as `now` for overlap purposes.
      2. Intersects [start, end] with [period_start, period_end).
      3. Drops sessions with zero or negative overlap.
      4. Recomputes duration_seconds from the CLIPPED start/end, not the
         original — so a session spanning a period boundary only
         contributes the portion that actually falls inside that period.

    period_start/period_end of None means unbounded on that side (used for
    "all"). Returns a new list — never mutates the input.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for s in sessions:
        s_start = s["start"]
        s_end = s["end"] if s["end"] is not None else now

        clip_start = max(s_start, period_start) if period_start else s_start
        clip_end = min(s_end, period_end) if period_end else s_end

        if clip_end <= clip_start:
            continue  # no overlap with this period at all

        out.append({
            **s,
            "start": clip_start,
            "end": clip_end,
            "duration_seconds": (clip_end - clip_start).total_seconds(),
        })
    return out


# ---------- 3. stat aggregation (operates on an already-clipped session list) ----------

def vc_time_totals(sessions, top=10):
    """Total VC time per user, sorted descending. Sessions should already be period-clipped."""
    totals = defaultdict(lambda: {"user_name": None, "total_seconds": 0.0})
    for s in sessions:
        entry = totals[s["user_id"]]
        entry["user_name"] = s["user_name"]
        entry["total_seconds"] += s["duration_seconds"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1]["total_seconds"], reverse=True)
    return [{"user_id": uid, **v} for uid, v in ranked[:top]]


def longest_sessions(sessions, top=10):
    """The single longest (clipped) session per user, sorted descending."""
    best = {}
    for s in sessions:
        uid = s["user_id"]
        if uid not in best or s["duration_seconds"] > best[uid]["duration_seconds"]:
            best[uid] = s
    ranked = sorted(best.values(), key=lambda s: s["duration_seconds"], reverse=True)
    return [
        {"user_id": s["user_id"], "user_name": s["user_name"], "channel_name": s["channel_name"],
         "duration_seconds": s["duration_seconds"]}
        for s in ranked[:top]
    ]


def _overlap_seconds(session, window_start_hour, window_end_hour):
    """Seconds of a (already period-clipped) session that fall within a daily
    local-hour window [start, end). Handles windows wrapping past midnight."""
    total = 0.0
    cur = session["start"]
    end = session["end"]

    while cur < end:
        day_start = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day_start = day_start + timedelta(days=1)
        day_slice_end = min(end, next_day_start)

        if window_start_hour <= window_end_hour:
            w_start = day_start + timedelta(hours=window_start_hour)
            w_end = day_start + timedelta(hours=window_end_hour)
            seg_start, seg_end = max(cur, w_start), min(day_slice_end, w_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()
        else:
            w1_start, w1_end = day_start + timedelta(hours=window_start_hour), next_day_start
            seg_start, seg_end = max(cur, w1_start), min(day_slice_end, w1_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()
            w2_start, w2_end = day_start, day_start + timedelta(hours=window_end_hour)
            seg_start, seg_end = max(cur, w2_start), min(day_slice_end, w2_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()

        cur = day_slice_end

    return total


def _shift_session(session, tz_offset_hours):
    if not tz_offset_hours:
        return session
    delta = timedelta(hours=tz_offset_hours)
    return {**session, "start": session["start"] + delta, "end": session["end"] + delta}


def night_owl_and_early_bird(sessions, tz_offset_hours=0):
    """Night owl: most time in 22:00-04:00 local. Early bird: most time in 05:00-09:00 local.
    Sessions should already be period-clipped (in UTC) before calling this."""
    night_totals = defaultdict(lambda: {"user_name": None, "seconds": 0.0})
    morning_totals = defaultdict(lambda: {"user_name": None, "seconds": 0.0})

    for s in sessions:
        shifted = _shift_session(s, tz_offset_hours)
        uid = s["user_id"]
        night_totals[uid]["user_name"] = s["user_name"]
        night_totals[uid]["seconds"] += _overlap_seconds(shifted, 22, 4)
        morning_totals[uid]["user_name"] = s["user_name"]
        morning_totals[uid]["seconds"] += _overlap_seconds(shifted, 5, 9)

    def top1(totals):
        candidates = [(uid, v) for uid, v in totals.items() if v["seconds"] > 0]
        if not candidates:
            return None
        uid, v = max(candidates, key=lambda kv: kv[1]["seconds"])
        return {"user_id": uid, "user_name": v["user_name"], "seconds": v["seconds"]}

    return {"night_owl": top1(night_totals), "early_bird": top1(morning_totals)}


def hour_of_day_totals(sessions, tz_offset_hours=0):
    """Total combined VC-seconds per local hour-of-day (0-23). Sessions should already be period-clipped."""
    totals = [0.0] * 24
    for s in sessions:
        shifted = _shift_session(s, tz_offset_hours)
        cur, end = shifted["start"], shifted["end"]
        while cur < end:
            hour_start = cur.replace(minute=0, second=0, microsecond=0)
            next_hour = hour_start + timedelta(hours=1)
            seg_end = min(end, next_hour)
            totals[cur.hour] += (seg_end - cur).total_seconds()
            cur = seg_end
    return totals


def channel_totals(sessions, top=10):
    """Total combined VC-seconds per channel. Sessions should already be period-clipped."""
    totals = defaultdict(float)
    for s in sessions:
        if s["channel_name"]:
            totals[s["channel_name"]] += s["duration_seconds"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [{"channel_name": name, "total_seconds": secs} for name, secs in ranked[:top]]


def compute_who_was_with_me(target_user_id, sessions, now=None, top=10):
    """
    For 'Who Was With Me?': finds every OTHER user who shared actual
    overlapping time with target_user_id, IN THE SAME CHANNEL — being in a
    different VC at the same time doesn't count as "together" (you can't
    hear them). Handles multiple sessions per user, different VCs, MOVE
    events (each MOVE already produces separate session rows, so this needs
    no special-casing), still-open sessions (end=None treated as `now`),
    and partial-overlap time boundaries via proper max/min clipping.

    sessions: the full session list (every user, not just the target),
        each shaped like vc_stats' usual session dicts.
    Returns a list of {user_id, user_name, seconds}, sorted descending,
    capped at `top`.
    """
    now = now or datetime.now(timezone.utc)
    target_sessions = [s for s in sessions if s["user_id"] == target_user_id]
    other_sessions = [s for s in sessions if s["user_id"] != target_user_id]

    totals = defaultdict(lambda: {"user_name": None, "seconds": 0.0})
    for ts in target_sessions:
        t_start = ts["start"]
        t_end = ts["end"] if ts["end"] is not None else now
        if t_end <= t_start:
            continue
        for os_ in other_sessions:
            if os_["channel_name"] != ts["channel_name"]:
                continue
            o_start = os_["start"]
            o_end = os_["end"] if os_["end"] is not None else now
            overlap_start = max(t_start, o_start)
            overlap_end = min(t_end, o_end)
            if overlap_end > overlap_start:
                entry = totals[os_["user_id"]]
                entry["user_name"] = os_["user_name"]
                entry["seconds"] += (overlap_end - overlap_start).total_seconds()

    ranked = sorted(totals.items(), key=lambda kv: kv[1]["seconds"], reverse=True)
    return [{"user_id": uid, "user_name": v["user_name"], "seconds": v["seconds"]} for uid, v in ranked[:top]]


def peak_concurrent_by_channel(sessions):
    """Peak simultaneous members per channel, via a +1/-1 sweep over (already
    period-clipped) session start/end times. Sessions, not raw events — this
    also naturally respects the requested period since the sweep only sees
    clipped boundaries."""
    channel_deltas = defaultdict(list)
    for s in sessions:
        if not s["channel_name"]:
            continue
        channel_deltas[s["channel_name"]].append((s["start"], 1))
        channel_deltas[s["channel_name"]].append((s["end"], -1))

    peaks = {}
    for channel, deltas in channel_deltas.items():
        # Closes before opens at a tied timestamp — standard half-open
        # interval semantics (like meeting-room scheduling): a session
        # ending at T and a different session starting at exactly T are
        # back-to-back, not concurrent, so they should never momentarily
        # read as 2 people. A cross-channel MOVE is unaffected by this
        # ordering entirely, since the close (old channel) and open (new
        # channel) land in two separate per-channel delta lists, never
        # the same sweep.
        deltas.sort(key=lambda d: (d[0], d[1]))
        running = 0
        peak = 0
        for _, delta in deltas:
            running += delta
            peak = max(peak, running)
        peaks[channel] = peak
    return peaks


def classify_vc_state(open_sessions, currently_present):
    """
    Smart Recovery's core comparison: DB session state vs. real Discord
    presence, classified into exactly one category per user — no DB or
    Discord access, pure dict math, fully unit-testable.

    open_sessions: dict of user_id -> channel_id for every vc_sessions row
        currently OPEN (ended_at IS NULL).
    currently_present: dict of user_id -> channel_id for everyone Discord
        says is ACTUALLY in a voice channel right now.

    Returns a list of {user_id, category, db_channel, discord_channel},
    one record per user_id appearing in EITHER input — this is deliberately
    exhaustive, including MATCHes, so a recovery report can show everyone
    the comparison touched, not just the ones needing action.

    Categories:
      MATCH    — DB says open in channel X, Discord confirms present in X.
                 No action needed.
      MISSING  — DB says open, Discord says not present at all. Stale
                 session (bot missed the LEAVE while offline) — close it.
      MISMATCH — DB says open in channel X, Discord says present in a
                 DIFFERENT channel Y. Close the stale X session, open a
                 fresh Y one (bot missed a MOVE, or a LEAVE+JOIN pair,
                 while offline).
      NEW      — Discord says present, DB has no open session at all (bot
                 missed the JOIN while offline, or this is the very first
                 time the bot has ever seen this member in voice).
    """
    records = []
    for uid in set(open_sessions) | set(currently_present):
        db_channel = open_sessions.get(uid)
        discord_channel = currently_present.get(uid)
        if db_channel is not None and discord_channel is not None:
            category = "match" if db_channel == discord_channel else "mismatch"
        elif db_channel is not None:
            category = "missing"
        else:
            category = "new"
        records.append({
            "user_id": uid, "category": category,
            "db_channel": db_channel, "discord_channel": discord_channel,
        })
    return records


def compute_reconciliation_actions(open_sessions, currently_present):
    """
    Pure decision logic for startup/reconnect reconciliation — built on top
    of classify_vc_state so there's one source of truth for the comparison
    itself; this function just turns categories into concrete actions.

    Returns (to_close, to_open) as sets of user_ids:
      to_close = MISSING or MISMATCH — the stale/old-channel session must close.
      to_open  = NEW or MISMATCH — a session must open at the current channel
                 (a user_id can legitimately appear in BOTH sets for MISMATCH:
                 close the old channel's session, open the new channel's).

    The caller is expected to open the new session using
    currently_present[uid] as the channel — this function only decides
    WHO needs an action, not the channel details, which is DB/Discord
    object data outside this pure module's concern.
    """
    to_close = set()
    to_open = set()
    for r in classify_vc_state(open_sessions, currently_present):
        if r["category"] == "missing":
            to_close.add(r["user_id"])
        elif r["category"] == "mismatch":
            to_close.add(r["user_id"])
            to_open.add(r["user_id"])
        elif r["category"] == "new":
            to_open.add(r["user_id"])
        # "match" -> no action
    return to_close, to_open


def format_duration(seconds):
    """1h 23m style formatting for display."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    if m:
        return f"{m}m"
    return f"{seconds}s"
