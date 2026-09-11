"""
VC Control — single-file build.

Everything lives here: the Discord bot, the Flask web server, PostgreSQL
persistence, VC-time analytics, and the frontend HTML/CSS/JS (inlined as
the INDEX_HTML string below and served via render_template_string).

This is a merged version of what's normally 4 files (app.py, db.py,
analytics.py, templates/index.html) for people who'd rather manage one
file. Functionally identical — same routes, same behavior, same tests
passing. If you'd rather have it split back into separate files for
easier editing, ask and it can be split again.
"""

import os
import io
import random
import asyncio
import threading
import time
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool

import discord
from discord import app_commands
import vc_stats as vs
from flask import Flask, render_template_string, jsonify, request


# ---------- CONFIG (set these as environment variables) ----------
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = int(os.environ["GUILD_ID"])
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")  # optional simple lock
ALLOWED_CATEGORY_ID = os.environ.get("ALLOWED_CATEGORY_ID")  # optional: restrict VC list to one category

# What happens when someone violates a VC lock: "disconnect" (kick them out of voice)
# or "move_back" (send them back to their assigned locked channel instead).
LOCK_VIOLATION_ACTION = os.environ.get("LOCK_VIOLATION_ACTION", "disconnect").lower()
if LOCK_VIOLATION_ACTION not in ("disconnect", "move_back"):
    LOCK_VIOLATION_ACTION = "disconnect"

# Hours to add to UTC to get local time, for the leaderboard/dashboard's night-owl,
# early-bird, and hour-of-day stats (e.g. 5.5 for India/IST). Fixed offset, not a
# full timezone database — doesn't handle DST, which is fine for IST since India
# doesn't observe it. Defaults to 0 (UTC) if unset.
try:
    TIMEZONE_OFFSET_HOURS = float(os.environ.get("TIMEZONE_OFFSET_HOURS", "0"))
except ValueError:
    TIMEZONE_OFFSET_HOURS = 0.0



# ============================================================
# FRONTEND (inlined so this is a single file)
# ============================================================
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>VC Control</title>
<style>
  :root {
    --bg: #12101c;
    --panel: #1d1a2e;
    --panel2: #26223a;
    --accent: #7c5cff;
    --accent-dark: #5b3fd9;
    --accent-light: #a78bfa;
    --text: #ece9f7;
    --muted: #8f8aa8;
    --border: #322d4a;
    --blue: #6fa8ff;
    --coral: #ff8f6f;
    --teal: #17d3a2;
    --danger: #ff4d6d;
  }
  * { box-sizing: border-box; -webkit-user-select: none; user-select: none; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 18px 16px 90px;
    min-height: 100vh;
    touch-action: pan-y;
  }
  h1 { font-size: 21px; letter-spacing: 0.3px; margin: 0 0 2px; font-weight: 700; }
  .sub { color: var(--muted); font-size: 12.5px; margin-bottom: 18px; }
  h2 { font-size: 12px; text-transform: uppercase; color: var(--muted); margin: 16px 0 8px; letter-spacing: 0.6px; font-weight: 700; }
  select {
    width: 100%; padding: 13px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); font-size: 15px; margin-bottom: 4px;
    -webkit-appearance: none; appearance: none;
  }
  .btn {
    width: 100%; padding: 16px; margin-bottom: 10px; border: none; border-radius: 12px;
    font-size: 15px; font-weight: 700; letter-spacing: 0.2px;
    color: white; background: var(--panel2); border: 1px solid var(--border);
    -webkit-tap-highlight-color: transparent;
  }
  .btn:active { transform: scale(0.97); }
  .btn.primary { background: var(--accent); border-color: var(--accent); }
  .btn.secondary { background: var(--panel2); }
  .btn.small { padding: 12px; font-size: 13px; }
  #pwbox { display: none; margin-bottom: 16px; }
  #pwbox input {
    width: 100%; padding: 12px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); font-size: 15px;
  }
  #result {
    margin-top: 16px; padding: 13px; background: var(--panel); border-radius: 12px;
    font-size: 13.5px; white-space: pre-wrap; min-height: 20px; border: 1px solid var(--border);
  }
  .moved-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .moved-chip {
    display: flex; align-items: center; gap: 6px; background: var(--bg);
    border: 1px solid var(--border); border-radius: 20px; padding: 4px 10px 4px 4px; font-size: 12.5px;
  }
  .moved-chip img { width: 20px; height: 20px; border-radius: 50%; }
  .zone-empty { font-size: 12px; color: var(--muted); padding: 4px 0; }

  /* ---- Tab bar ---- */
  .view { display: none; }
  .view.active { display: block; }
  .tabbar {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 50;
    display: flex; background: var(--panel); border-top: 1px solid var(--border);
    padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px));
  }
  .tab {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
    padding: 6px 2px; color: var(--muted); font-size: 10px; font-weight: 600;
    -webkit-tap-highlight-color: transparent;
  }
  .tab.active { color: var(--accent-light); }
  .tab .tab-icon { font-size: 18px; line-height: 1; }

  /* ---- Stat cards ---- */
  .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 6px; }
  .stat-card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px;
  }
  .stat-card .stat-label { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px; }
  .stat-card .stat-value { font-size: 21px; font-weight: 700; margin-top: 3px; color: var(--accent-light); }
  .stat-card .stat-sub { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

  /* ---- Player cards / zones (Teams tab) ---- */
  .zone {
    border-radius: 12px; padding: 10px; margin-bottom: 10px;
    background: var(--panel); border: 1.5px dashed var(--border);
    min-height: 56px; transition: border-color 0.15s, background 0.15s;
  }
  .zone-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 8px; }
  .zone.team-a-zone { border-color: var(--blue); }
  .zone.team-a-zone .zone-label { color: var(--blue); }
  .zone.team-b-zone { border-color: var(--coral); }
  .zone.team-b-zone .zone-label { color: var(--coral); }
  .zone.drop-hover { background: var(--panel2); }

  .player-card {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 10px; border-radius: 10px; margin-bottom: 6px;
    background: var(--bg); border: 1px solid var(--border);
    touch-action: none; cursor: grab;
  }
  .player-card img { width: 30px; height: 30px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .player-card .name { flex: 1; font-size: 13.5px; }
  .player-card .drag-hint { color: var(--muted); font-size: 12px; flex-shrink: 0; }
  .player-card.dragging { opacity: 0.35; }
  .disconnect-btn {
    width: 26px; height: 26px; border-radius: 8px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; background: #2a1420; border: 1px solid #4a2035; color: var(--danger);
    touch-action: manipulation; cursor: pointer;
  }
  .drag-ghost {
    position: fixed; z-index: 999; pointer-events: none;
    display: flex; align-items: center; gap: 10px;
    padding: 9px 14px; border-radius: 10px;
    background: var(--panel2); border: 1px solid var(--accent);
    font-size: 13.5px; box-shadow: 0 8px 20px rgba(0,0,0,0.5);
  }
  .drag-ghost img { width: 30px; height: 30px; border-radius: 50%; }
  .refresh-note { font-size: 11px; color: var(--muted); margin: 4px 0 2px; text-align: right; }

  /* ---- Lock tab ---- */
  .lock-channel-row {
    display: flex; align-items: center; gap: 10px;
    padding: 10px; border-radius: 10px; margin-bottom: 6px;
    background: var(--panel); border: 1px solid var(--border);
  }
  .lock-channel-row.is-locked { border-color: var(--teal); background: #10241d; }
  .lock-channel-row input[type="checkbox"] { width: 18px; height: 18px; flex-shrink: 0; }
  .lock-channel-row .lc-name { flex: 1; font-size: 13.5px; }
  .lock-channel-row .lc-count { font-size: 11px; color: var(--muted); }
  .lock-badge {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    padding: 3px 8px; border-radius: 6px; background: var(--teal); color: #0f1923; flex-shrink: 0;
  }
  .unlock-one-btn {
    font-size: 11px; padding: 5px 9px; border-radius: 6px;
    background: #2a1420; border: 1px solid #4a2035; color: var(--danger); flex-shrink: 0; cursor: pointer;
  }
  .lock-status-line { font-size: 12px; color: var(--muted); margin: 6px 0 10px; }

  /* ---- Stats tab ---- */
  .period-tabs { display: flex; gap: 6px; margin-bottom: 10px; overflow-x: auto; }
  .period-tab {
    flex-shrink: 0; padding: 8px 14px; border-radius: 20px; font-size: 12.5px; font-weight: 700;
    background: var(--panel); border: 1px solid var(--border); color: var(--muted);
  }
  .period-tab.active { background: var(--accent); border-color: var(--accent); color: white; }
  .lb-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px; margin-bottom: 10px; }
  .lb-title { font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.5px; margin-bottom: 8px; font-weight: 700; }
  .lb-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; font-size: 13.5px; }
  .lb-rank { width: 18px; color: var(--muted); font-size: 12px; flex-shrink: 0; }
  .lb-name { flex: 1; }
  .lb-value { color: var(--accent-light); font-weight: 700; font-size: 12.5px; }
  .lb-highlight { display: flex; align-items: center; gap: 10px; }
  .lb-highlight .lb-emoji { font-size: 22px; }
  .lb-highlight .lb-name { font-size: 14px; font-weight: 700; }
  .lb-highlight .lb-sub { font-size: 11px; color: var(--muted); }
  .hour-chart { display: flex; align-items: flex-end; gap: 2px; height: 70px; margin: 10px 0; }
  .hour-bar { flex: 1; background: var(--accent); border-radius: 2px 2px 0 0; min-height: 2px; opacity: 0.65; }
  .hour-bar.peak { opacity: 1; }
  .hour-labels { display: flex; justify-content: space-between; font-size: 9px; color: var(--muted); margin-bottom: 12px; }

  /* ---- Log tab ---- */
  .log-entry {
    display: flex; align-items: center; gap: 8px;
    padding: 8px; border-radius: 8px; margin-bottom: 6px;
    background: var(--panel); border: 1px solid var(--border); font-size: 12.5px;
  }
  .log-entry img { width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0; }
  .log-avatar-placeholder {
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    background: var(--panel2); display: flex; align-items: center; justify-content: center;
    font-size: 10px; color: var(--muted);
  }
  .log-entry .log-text { flex: 1; }
  .log-entry .log-time { color: var(--muted); font-size: 11px; flex-shrink: 0; }
  .log-tag { font-weight: 700; }
  .log-tag.joined { color: var(--teal); }
  .log-tag.left { color: var(--coral); }
  .log-tag.moved { color: var(--blue); }
  .log-tag.disconnected { color: var(--danger); }
  .log-tag.violation { color: #ffb84d; }
  .log-tag.lock { color: var(--teal); }
  .log-entry .log-actor { color: var(--muted); font-style: italic; }
</style>
<style>
.switch { position: relative; display: inline-block; width: 44px; height: 24px; flex-shrink: 0; }
.switch input { opacity: 0; width: 0; height: 0; }
.switch .slider { position: absolute; cursor: pointer; inset: 0; background-color: #444; border-radius: 24px; transition: .15s; }
.switch .slider::before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; border-radius: 50%; transition: .15s; }
.switch input:checked + .slider { background-color: #ff4655; }
.switch input:checked + .slider::before { transform: translateX(20px); }
</style>
</head>
<body>
  <h1>VC control</h1>
  <div class="sub">Manage your Valorant voice channels</div>

  <div id="pwbox">
    <input type="password" id="pw" placeholder="Password (needed to move/lock)" onkeydown="if(event.key==='Enter') unlock()">
    <button class="btn primary" style="margin-top:8px;" onclick="unlock()">Save password</button>
  </div>

  <!-- ============ DASHBOARD ============ -->
  <div class="view active" id="view-dashboard">
    <h2>Overview</h2>
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">People in voice</div><div class="stat-value" id="statPeople">—</div></div>
      <div class="stat-card"><div class="stat-label">Voice channels</div><div class="stat-value" id="statChannels">—</div></div>
      <div class="stat-card"><div class="stat-label">Locked channels</div><div class="stat-value" id="statLocked">—</div></div>
      <div class="stat-card"><div class="stat-label">VC time today</div><div class="stat-value" id="statTimeToday">—</div></div>
    </div>

    <h2 style="margin-top:20px;">Recent activity</h2>
    <div id="dashRecentLog"><div class="zone-empty">Loading…</div></div>
  </div>

  <!-- ============ TEAMS ============ -->
  <div class="view" id="view-teams">
    <h2>Lobby</h2>
    <select id="lobbySelect" onchange="loadPlayers()"></select>
    <h2>Team A channel</h2>
    <select id="teamASelect"></select>
    <h2>Team B channel</h2>
    <select id="teamBSelect"></select>

    <h2>Players</h2>
    <div class="refresh-note" id="refreshNote">Auto-refreshing every 10s</div>
    <div class="zone team-a-zone" data-zone="a" id="zoneA">
      <div class="zone-label">Team A</div>
      <div class="zone-body"></div>
    </div>
    <div class="zone" data-zone="lobby" id="zoneLobby" style="border-color:var(--border);">
      <div class="zone-label">Unassigned</div>
      <div class="zone-body"></div>
    </div>
    <div class="zone team-b-zone" data-zone="b" id="zoneB">
      <div class="zone-label">Team B</div>
      <div class="zone-body"></div>
    </div>

    <div style="display:flex;gap:10px;margin-top:12px;">
      <button class="btn" style="background:var(--blue);border-color:var(--blue);flex:1;" onclick="move('a')">Move → A</button>
      <button class="btn" style="background:var(--coral);border-color:var(--coral);flex:1;" onclick="move('b')">Move → B</button>
    </div>
    <button class="btn secondary" onclick="rematch()">🔁 Rematch (swap sides)</button>
    <button class="btn secondary" onclick="regroup()">🏠 Regroup to lobby</button>
    <button class="btn secondary small" onclick="loadChannels()">↻ Refresh channel list</button>

    <div id="result">Pick your VCs, then drag names into Team A or B.</div>
  </div>

  <!-- ============ LOCK ============ -->
  <div class="view" id="view-lock">
    <h2>VC lock</h2>
    <div class="lock-status-line" id="lockStatusLine">Loading lock status…</div>
    <div id="lockChannelList"></div>
    <div style="display:flex;gap:10px;margin-top:6px;">
      <button class="btn" style="background:var(--teal);color:#0f1923;border-color:var(--teal);flex:1;font-size:14px;" onclick="lockSelected()">🔒 Lock selected</button>
      <button class="btn secondary" style="flex:1;font-size:14px;" onclick="unlockAll()">🔓 Unlock all</button>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-top:8px;">
      Locking a channel snapshots who's inside it right now — they can leave and rejoin freely, but can't switch into another locked channel, and no one outside the snapshot can join it.
    </div>
    <div id="lockResult" style="margin-top:12px;"></div>
  </div>

  <!-- ============ STATS ============ -->
  <div class="view" id="view-stats">
    <h2>Leaderboard</h2>
    <div class="period-tabs" id="periodTabs">
      <button class="period-tab active" data-period="all" onclick="setPeriod('all')">All time</button>
      <button class="period-tab" data-period="month" onclick="setPeriod('month')">This month</button>
      <button class="period-tab" data-period="week" onclick="setPeriod('week')">This week</button>
      <button class="period-tab" data-period="today" onclick="setPeriod('today')">Today</button>
    </div>
    <div id="leaderboardBody"><div class="zone-empty">Loading…</div></div>

    <h2 style="margin-top:20px;">Dashboard</h2>
    <div id="dashboardBody"><div class="zone-empty">Loading…</div></div>
  </div>

  <!-- ============ LOG ============ -->
  <div class="view" id="view-log">
    <h2>Activity log</h2>
    <button class="btn secondary small" onclick="loadLog()">↻ Refresh log</button>
    <div id="logList" style="margin-top:8px;"></div>
  </div>

  <div class="view" id="view-settings">
    <h2>Feature toggles</h2>
    <div id="togglesList" style="margin-top:8px;"></div>

    <h2 style="margin-top:20px;">Birthday reminder interval</h2>
    <p class="muted" style="font-size:13px; margin:4px 0 10px;">Only applies while "Repeat birthday reminders on an interval" above is ON. When it's OFF, members get nudged exactly once, ever.</p>
    <div style="display:flex; gap:8px; align-items:center;">
      <input type="number" id="reminderValue" min="0.01" step="any" style="width:90px;" placeholder="7">
      <select id="reminderUnit" style="padding:8px;">
        <option value="minutes">minutes</option>
        <option value="hours">hours</option>
        <option value="days" selected>days</option>
        <option value="years">years</option>
      </select>
      <button class="btn small" onclick="saveBirthdayReminderInterval()">Save</button>
    </div>
    <div id="reminderSaveMsg" class="muted" style="font-size:12px; margin-top:6px;"></div>
  </div>

  <div class="tabbar">
    <div class="tab active" data-tab="dashboard" onclick="switchTab('dashboard')"><span class="tab-icon">📊</span>Dashboard</div>
    <div class="tab" data-tab="teams" onclick="switchTab('teams')"><span class="tab-icon">🎮</span>Teams</div>
    <div class="tab" data-tab="lock" onclick="switchTab('lock')"><span class="tab-icon">🔒</span>Lock</div>
    <div class="tab" data-tab="stats" onclick="switchTab('stats')"><span class="tab-icon">🏆</span>Stats</div>
    <div class="tab" data-tab="log" onclick="switchTab('log')"><span class="tab-icon">📋</span>Log</div>
    <div class="tab" data-tab="settings" onclick="switchTab('settings')"><span class="tab-icon">⚙️</span>Settings</div>
  </div>

<script>
const siteLocked = {{ 'true' if locked else 'false' }};
const resultEl = document.getElementById('result');
let channels = [];
let players = [];
let assignments = {};
let currentTab = 'dashboard';
let currentPeriod = 'all';
let dragState = null;

function pw() { const el = document.getElementById('pw'); return el ? el.value : ''; }

function updatePwboxVisibility() {
  const box = document.getElementById('pwbox');
  if (!box) return;
  const needsPassword = currentTab === 'teams' || currentTab === 'lock' || currentTab === 'settings';
  box.style.display = (siteLocked && needsPassword) ? 'block' : 'none';
}

async function apiGet(path) {
  const res = await fetch(path, { headers: { 'X-PW': pw() } });
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 401) return null;
    return null;
  }
  return data;
}

async function apiPost(path, body) {
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PW': pw() },
      body: JSON.stringify(body || {})
    });
    const data = await res.json();
    if (!res.ok) return { __error: data.error || 'Something went wrong' };
    return data;
  } catch (e) {
    return { __error: 'Network error' };
  }
}

async function unlock() {
  await loadChannels();
  await loadPlayers();
  await refreshCurrentTab();
}

// ---------- Tab switching ----------

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('view-' + tab).classList.add('active');
  document.querySelector(`.tab[data-tab="${tab}"]`).classList.add('active');
  updatePwboxVisibility();
  refreshCurrentTab();
}

async function refreshCurrentTab() {
  if (currentTab === 'dashboard') { loadDashboardStats(); }
  else if (currentTab === 'teams') { loadChannels(); loadPlayers(); }
  else if (currentTab === 'lock') { loadLockPanel(); }
  else if (currentTab === 'stats') { loadLeaderboard(); loadDashboard(); }
  else if (currentTab === 'log') { loadLog(); }
  else if (currentTab === 'settings') { loadToggles(); loadBirthdayReminderInterval(); }
}

// ---------- Settings tab ----------

async function loadToggles() {
  const data = await apiGet('/api/toggles');
  const box = document.getElementById('togglesList');
  if (!data) { box.innerHTML = '<p class="muted">Could not load toggles.</p>'; return; }
  box.innerHTML = data.toggles.map(t => `
    <div style="display:flex; align-items:center; justify-content:space-between; padding:12px 0; border-bottom:1px solid #2a2a2a;">
      <div>
        <div style="font-weight:600;">${t.description}</div>
        <div class="muted" style="font-size:12px;">${t.key}</div>
      </div>
      <label class="switch">
        <input type="checkbox" ${t.enabled ? 'checked' : ''} onchange="toggleFeature('${t.key}', this.checked)">
        <span class="slider"></span>
      </label>
    </div>
  `).join('');
}

async function toggleFeature(key, enabled) {
  const res = await apiPost('/api/toggles/' + key, { enabled });
  if (res && res.__error) { alert(res.__error); loadToggles(); }
}

async function loadBirthdayReminderInterval() {
  const data = await apiGet('/api/birthday-reminder-interval');
  if (!data) return;
  document.getElementById('reminderValue').value = data.value;
  document.getElementById('reminderUnit').value = data.unit;
}

async function saveBirthdayReminderInterval() {
  const value = parseFloat(document.getElementById('reminderValue').value);
  const unit = document.getElementById('reminderUnit').value;
  const msg = document.getElementById('reminderSaveMsg');
  if (!value || value <= 0) { msg.textContent = 'Enter a value greater than 0.'; return; }
  const res = await apiPost('/api/birthday-reminder-interval', { value, unit });
  if (res && res.__error) { msg.textContent = res.__error; return; }
  msg.textContent = `Saved — reminders every ${value} ${unit}.`;
}


// ---------- Dashboard tab ----------

function fmtDur(seconds) {
  const s = Math.round(seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

async function loadDashboardStats() {
  const chData = await apiGet('/api/channels');
  if (chData) {
    channels = chData.channels;
    const totalPeople = channels.reduce((sum, c) => sum + c.member_count, 0);
    document.getElementById('statPeople').textContent = totalPeople;
    document.getElementById('statChannels').textContent = channels.length;
    document.getElementById('statLocked').textContent = channels.filter(c => c.locked).length;
  }

  const dashData = await apiGet('/api/analytics/dashboard?period=today');
  if (dashData && dashData.enabled) {
    const totalToday = dashData.hour_of_day_seconds.reduce((a, b) => a + b, 0);
    document.getElementById('statTimeToday').textContent = fmtDur(totalToday);
  } else {
    document.getElementById('statTimeToday').textContent = '—';
  }

  const logData = await apiGet('/api/voice_log?limit=8');
  const el = document.getElementById('dashRecentLog');
  if (logData && logData.events.length) {
    el.innerHTML = logData.events.map(logLine).join('');
  } else {
    el.innerHTML = '<div class="zone-empty">No activity yet</div>';
  }
}

// ---------- Teams tab ----------

function fillSelect(el, list, keepValue) {
  const prev = keepValue ? el.value : null;
  el.innerHTML = list.map(c => `<option value="${c.id}">${c.name} (${c.member_count})</option>`).join('');
  if (prev && list.some(c => c.id === prev)) el.value = prev;
}

async function loadChannels() {
  const data = await apiGet('/api/channels');
  if (!data) return;
  channels = data.channels;
  fillSelect(document.getElementById('lobbySelect'), channels, true);
  fillSelect(document.getElementById('teamASelect'), channels, true);
  fillSelect(document.getElementById('teamBSelect'), channels, true);
}

async function loadPlayers() {
  const lobbyId = document.getElementById('lobbySelect').value;
  if (!lobbyId) return;
  const data = await apiGet('/api/lobby_members?lobby_id=' + lobbyId);
  if (!data) return;
  const newPlayers = data.members;
  const newIds = new Set(newPlayers.map(p => p.id));
  Object.keys(assignments).forEach(id => { if (!newIds.has(id)) delete assignments[id]; });
  players = newPlayers;
  renderZones();
}

function renderZones() {
  const zoneLobby = document.querySelector('#zoneLobby .zone-body');
  const zoneA = document.querySelector('#zoneA .zone-body');
  const zoneB = document.querySelector('#zoneB .zone-body');
  zoneLobby.innerHTML = ''; zoneA.innerHTML = ''; zoneB.innerHTML = '';

  const groups = { lobby: [], a: [], b: [] };
  players.forEach(p => {
    const t = assignments[p.id] === 'a' ? 'a' : assignments[p.id] === 'b' ? 'b' : 'lobby';
    groups[t].push(p);
  });

  const targets = { lobby: zoneLobby, a: zoneA, b: zoneB };
  Object.keys(groups).forEach(key => {
    if (groups[key].length === 0) {
      targets[key].innerHTML = '<div class="zone-empty">Drop players here</div>';
      return;
    }
    groups[key].forEach(p => {
      const card = document.createElement('div');
      card.className = 'player-card';
      card.dataset.playerId = p.id;
      card.innerHTML = `<img src="${p.avatar}" alt=""><div class="name">${p.name}</div><div class="disconnect-btn" data-disconnect-id="${p.id}" title="Disconnect">⏻</div><div class="drag-hint">⠿</div>`;
      attachDrag(card, p.id);
      card.querySelector('.disconnect-btn').addEventListener('pointerdown', (e) => e.stopPropagation());
      card.querySelector('.disconnect-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        disconnectPlayer(p.id, p.name);
      });
      targets[key].appendChild(card);
    });
  });
}

function attachDrag(card, playerId) {
  card.addEventListener('pointerdown', (e) => {
    if (e.target.closest('.disconnect-btn')) return;
    e.preventDefault();
    const rect = card.getBoundingClientRect();
    const ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    ghost.style.width = rect.width + 'px';
    ghost.innerHTML = card.innerHTML;
    ghost.style.left = rect.left + 'px';
    ghost.style.top = rect.top + 'px';
    document.body.appendChild(ghost);
    card.classList.add('dragging');

    dragState = { playerId, ghost, offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top };

    const onMove = (ev) => {
      if (!dragState) return;
      ghost.style.left = (ev.clientX - dragState.offsetX) + 'px';
      ghost.style.top = (ev.clientY - dragState.offsetY) + 'px';
      document.querySelectorAll('.zone').forEach(z => z.classList.remove('drop-hover'));
      const under = document.elementFromPoint(ev.clientX, ev.clientY);
      const zoneEl = under && under.closest('.zone');
      if (zoneEl) zoneEl.classList.add('drop-hover');
    };

    const onUp = (ev) => {
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      if (!dragState) return;
      const under = document.elementFromPoint(ev.clientX, ev.clientY);
      const zoneEl = under && under.closest('.zone');
      document.querySelectorAll('.zone').forEach(z => z.classList.remove('drop-hover'));
      ghost.remove();
      if (zoneEl) {
        const zoneKey = zoneEl.dataset.zone;
        if (zoneKey === 'lobby') delete assignments[dragState.playerId];
        else assignments[dragState.playerId] = zoneKey;
      }
      dragState = null;
      renderZones();
    };

    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  });
}

function movedChip(p) { return '<div class="moved-chip"><img src="' + p.avatar + '" alt=""><span>' + p.name + '</span></div>'; }

function failedHtml(failed) {
  if (!failed || failed.length === 0) return '';
  return '<div style="margin-top:10px;color:var(--coral);font-size:13px;">⚠️ Failed:<br>' +
    failed.map(f => f.name + ' — ' + f.reason).join('<br>') + '</div>';
}

async function move(team) {
  const ids = Object.keys(assignments).filter(id => assignments[id] === team);
  if (ids.length === 0) {
    resultEl.textContent = `⚠️ No one assigned to Team ${team.toUpperCase()} yet — drag names into that zone first`;
    return;
  }
  const channelId = team === 'a'
    ? document.getElementById('teamASelect').value
    : document.getElementById('teamBSelect').value;
  const data = await apiPost('/api/move/' + team, { channel_id: channelId, member_ids: ids });
  if (!data || data.__error) { resultEl.textContent = '⚠️ ' + (data ? data.__error : 'Error'); return; }
  resultEl.innerHTML = '✅ Moved<div class="moved-list">' + data.moved.map(movedChip).join('') + '</div>' + failedHtml(data.failed);
}

async function rematch() {
  const teamAChannel = document.getElementById('teamASelect').value;
  const teamBChannel = document.getElementById('teamBSelect').value;
  const data = await apiPost('/api/rematch', { team_a_channel: teamAChannel, team_b_channel: teamBChannel });
  if (!data || data.__error) { resultEl.textContent = '⚠️ ' + (data ? data.__error : 'Error'); return; }
  resultEl.innerHTML = '✅ Sides swapped' + failedHtml(data.failed);
}

async function regroup() {
  const lobbyId = document.getElementById('lobbySelect').value;
  const teamAChannel = document.getElementById('teamASelect').value;
  const teamBChannel = document.getElementById('teamBSelect').value;
  const data = await apiPost('/api/regroup', { from_channel_ids: [teamAChannel, teamBChannel], to_channel_id: lobbyId });
  if (!data || data.__error) { resultEl.textContent = '⚠️ ' + (data ? data.__error : 'Error'); return; }
  resultEl.innerHTML = '✅ Back in lobby<div class="moved-list">' + data.moved.map(movedChip).join('') + '</div>' + failedHtml(data.failed);
}

async function disconnectPlayer(id, name) {
  const data = await apiPost('/api/disconnect', { member_ids: [id] });
  if (!data || data.__error) { resultEl.textContent = '⚠️ Could not disconnect'; return; }
  if (data.disconnected && data.disconnected.length) {
    resultEl.textContent = `🔌 Disconnected ${name}`;
    loadPlayers();
  }
}

// ---------- Lock tab ----------

function lockRow(c) {
  const locked = c.locked;
  return `<div class="lock-channel-row ${locked ? 'is-locked' : ''}">
    <input type="checkbox" value="${c.id}" ${locked ? 'disabled' : ''} class="lock-checkbox">
    <div class="lc-name">${c.name}<div class="lc-count">${c.member_count} in channel</div></div>
    ${locked ? `<span class="lock-badge">Locked</span><button class="unlock-one-btn" onclick="unlockOne('${c.id}')">Unlock</button>` : ''}
  </div>`;
}

async function loadLockPanel() {
  const data = await apiGet('/api/channels');
  if (!data) return;
  const el = document.getElementById('lockChannelList');
  el.innerHTML = data.channels.map(lockRow).join('');

  const status = await apiGet('/api/lock_status');
  const line = document.getElementById('lockStatusLine');
  if (status) {
    line.textContent = status.active
      ? `🔒 ${status.channels.length} channel(s) locked, ${status.total_locked_members} member(s) assigned`
      : 'No channels locked right now.';
  }
}

async function lockSelected() {
  const ids = Array.from(document.querySelectorAll('.lock-checkbox:checked')).map(c => c.value);
  const resEl = document.getElementById('lockResult');
  if (ids.length === 0) { resEl.textContent = '⚠️ Check at least one channel to lock'; return; }
  const data = await apiPost('/api/lock', { channel_ids: ids });
  if (!data || data.__error) { resEl.textContent = '⚠️ ' + (data ? data.__error : 'Error'); return; }
  resEl.textContent = `🔒 Locked ${ids.length} channel(s)`;
  loadLockPanel();
}

async function unlockOne(channelId) {
  const data = await apiPost('/api/unlock', { channel_ids: [channelId] });
  document.getElementById('lockResult').textContent = '🔓 Channel unlocked';
  loadLockPanel();
}

async function unlockAll() {
  const data = await apiPost('/api/unlock', {});
  document.getElementById('lockResult').textContent = '🔓 All channels unlocked';
  loadLockPanel();
}

// ---------- Stats tab ----------

function medal(i) { return i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : `${i + 1}.`; }

function setPeriod(period) {
  currentPeriod = period;
  document.querySelectorAll('.period-tab').forEach(t => t.classList.toggle('active', t.dataset.period === period));
  loadLeaderboard();
  loadDashboard();
}

async function loadLeaderboard() {
  const el = document.getElementById('leaderboardBody');
  const data = await apiGet('/api/leaderboard?period=' + currentPeriod);
  if (!data) return;
  if (!data.enabled) { el.innerHTML = `<div class="zone-empty">${data.message}</div>`; return; }
  let html = '';
  html += '<div class="lb-card"><div class="lb-title">🏆 Most time in VC</div>';
  html += data.vc_time.length
    ? data.vc_time.map((r, i) => `<div class="lb-row"><span class="lb-rank">${medal(i)}</span><span class="lb-name">${r.user_name}</span><span class="lb-value">${r.formatted}</span></div>`).join('')
    : '<div class="zone-empty">No data for this period</div>';
  html += '</div>';
  html += '<div class="lb-card"><div class="lb-title">⏱️ Longest single session</div>';
  html += data.longest_session.length
    ? data.longest_session.map((r, i) => `<div class="lb-row"><span class="lb-rank">${medal(i)}</span><span class="lb-name">${r.user_name} <span style="color:var(--muted);font-size:11px;">(${r.channel_name || '—'})</span></span><span class="lb-value">${r.formatted}</span></div>`).join('')
    : '<div class="zone-empty">No data for this period</div>';
  html += '</div>';
  if (data.night_owl || data.early_bird) {
    html += '<div class="lb-card">';
    if (data.night_owl) html += `<div class="lb-highlight" style="margin-bottom:10px;"><span class="lb-emoji">🌙</span><div><div class="lb-name">${data.night_owl.user_name}</div><div class="lb-sub">Night Owl · ${data.night_owl.formatted} between 10pm–4am</div></div></div>`;
    if (data.early_bird) html += `<div class="lb-highlight"><span class="lb-emoji">☀️</span><div><div class="lb-name">${data.early_bird.user_name}</div><div class="lb-sub">Early Bird · ${data.early_bird.formatted} between 5am–9am</div></div></div>`;
    html += '</div>';
  }
  el.innerHTML = html;
}

async function loadDashboard() {
  const el = document.getElementById('dashboardBody');
  const data = await apiGet('/api/analytics/dashboard?period=' + currentPeriod);
  if (!data) return;
  if (!data.enabled) { el.innerHTML = `<div class="zone-empty">${data.message}</div>`; return; }
  const maxHour = Math.max(...data.hour_of_day_seconds, 1);
  const hourBars = data.hour_of_day_seconds.map((v, h) => {
    const pct = Math.max(2, (v / maxHour) * 100);
    const isPeak = v === maxHour && v > 0;
    return `<div class="hour-bar ${isPeak ? 'peak' : ''}" style="height:${pct}%" title="${h}:00 — ${fmtDur(v)}"></div>`;
  }).join('');
  let html = '<div class="lb-card"><div class="lb-title">Activity by hour of day</div>';
  html += `<div class="hour-chart">${hourBars}</div>`;
  html += '<div class="hour-labels"><span>12am</span><span>6am</span><span>12pm</span><span>6pm</span><span>11pm</span></div></div>';
  html += '<div class="lb-card"><div class="lb-title">Most active channels</div>';
  html += data.channel_totals.length
    ? data.channel_totals.map((c, i) => `<div class="lb-row"><span class="lb-rank">${medal(i)}</span><span class="lb-name">${c.channel_name}</span><span class="lb-value">${c.formatted}</span></div>`).join('')
    : '<div class="zone-empty">No data for this period</div>';
  html += '</div>';
  const peakEntries = Object.entries(data.peak_concurrent || {});
  if (peakEntries.length) {
    html += '<div class="lb-card"><div class="lb-title">Peak concurrent members</div>';
    html += peakEntries.map(([name, count]) => `<div class="lb-row"><span class="lb-name">${name}</span><span class="lb-value">${count}</span></div>`).join('');
    html += '</div>';
  }
  html += `<div style="font-size:11px;color:var(--muted);margin-top:4px;">Covers all recorded history (${data.total_sessions_analyzed} session(s) in this period).</div>`;
  el.innerHTML = html;
}

// ---------- Log tab ----------

function timeAgo(iso) {
  const diffSec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (diffSec < 60) return diffSec + 's ago';
  const m = Math.floor(diffSec / 60);
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  return h + 'h ago';
}

function logLine(e) {
  const action = (e.action || e.event || '').toUpperCase();
  let text = '';
  if (action === 'JOINED') text = `<span class="log-tag joined">Joined</span> ${e.to || ''}`;
  else if (action === 'LEFT') text = `<span class="log-tag left">Left</span> ${e.from || ''}`;
  else if (action === 'DISCONNECTED') text = `<span class="log-tag disconnected">Disconnected</span> from ${e.from || ''}`;
  else if (action === 'MOVED') text = `<span class="log-tag moved">Moved</span> ${e.from || ''} → ${e.to || ''}`;
  else if (action === 'LOCK_VIOLATION') text = `<span class="log-tag violation">Lock violation</span> tried ${e.to || 'a locked channel'}`;
  else if (action === 'LOCK_ENABLED') text = `<span class="log-tag lock">Locked</span> ${e.details || ''}`;
  else if (action === 'LOCK_DISABLED') text = `<span class="log-tag lock">Unlocked</span> ${e.details || ''}`;
  else text = `<span class="log-tag">${action}</span> ${e.details || ''}`;
  const actorHtml = e.actor_name ? ` <span class="log-actor">— by ${e.actor_name}</span>` : '';
  const avatarHtml = e.avatar ? `<img src="${e.avatar}" alt="">` : `<div class="log-avatar-placeholder">●</div>`;
  const nameHtml = e.name ? `<b>${e.name}</b> — ` : '';
  return `<div class="log-entry">${avatarHtml}<div class="log-text">${nameHtml}${text}${actorHtml}</div><div class="log-time">${timeAgo(e.time)}</div></div>`;
}

async function loadLog() {
  const data = await apiGet('/api/voice_log?limit=50');
  if (!data) return;
  const el = document.getElementById('logList');
  el.innerHTML = data.events.length ? data.events.map(logLine).join('') : '<div class="zone-empty">No activity yet</div>';
}

// ---------- Background refresh ----------

setInterval(() => { if (!dragState) refreshCurrentTab(); }, 10000);

updatePwboxVisibility();
unlock();
</script>
</body>
</html>
"""



# ============================================================
# DATABASE (PostgreSQL persistence)
# ============================================================
import psycopg2
import psycopg2.extras
from psycopg2 import pool

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool = None


def init_db():
    """Create the connection pool and required tables. Safe to call once at startup."""
    global _pool
    if not DATABASE_URL:
        print("[db] DATABASE_URL not set — logs and locks will NOT persist across restarts.", flush=True)
        return False
    try:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL, sslmode="require")
        with _cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vc_logs (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    action TEXT NOT NULL,
                    user_id TEXT,
                    user_name TEXT,
                    actor_id TEXT,
                    actor_name TEXT,
                    from_channel_id TEXT,
                    from_channel_name TEXT,
                    to_channel_id TEXT,
                    to_channel_name TEXT,
                    details TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vc_lock_channels (
                    channel_id TEXT PRIMARY KEY,
                    channel_name TEXT,
                    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vc_lock_members (
                    user_id TEXT PRIMARY KEY,
                    allowed_channel_id TEXT NOT NULL,
                    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS join_requests (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    resolved_by_id TEXT,
                    resolved_by_name TEXT,
                    resolved_at TIMESTAMPTZ
                )
            """)
            # Safe to run every startup even on an existing table — no-ops if columns already exist
            cur.execute("ALTER TABLE join_requests ADD COLUMN IF NOT EXISTS resolved_by_id TEXT")
            cur.execute("ALTER TABLE join_requests ADD COLUMN IF NOT EXISTS resolved_by_name TEXT")
            cur.execute("ALTER TABLE join_requests ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    user_id TEXT PRIMARY KEY,
                    day INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    year INTEGER,
                    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_wished_year INTEGER
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS birthday_nudges (
                    user_id TEXT PRIMARY KEY,
                    last_nudged_at TIMESTAMPTZ NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS points (
                    user_id TEXT PRIMARY KEY,
                    balance BIGINT NOT NULL DEFAULT 0,
                    last_daily_claim DATE
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vc_sessions (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    user_name TEXT,
                    channel_id TEXT,
                    channel_name TEXT,
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ,
                    duration_seconds DOUBLE PRECISION,
                    start_is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
                    end_is_estimated BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            # Indexes for the two access patterns that matter: "give me this
            # user's sessions" and "give me every session overlapping [a, b)".
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vc_sessions_user_started ON vc_sessions (user_id, started_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vc_sessions_started ON vc_sessions (started_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vc_sessions_ended ON vc_sessions (ended_at)")
            # Hard DB-level invariant: at most one OPEN session per user, ever.
            # open_or_move_session()/close_session() already enforce this via
            # atomic close-then-insert, but this is the safety net if anything
            # else ever writes to this table directly.
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_vc_sessions_one_open_per_user "
                "ON vc_sessions (user_id) WHERE ended_at IS NULL"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vc_reservations (
                    id BIGSERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    user_name TEXT,
                    channel_id TEXT NOT NULL,
                    channel_name TEXT,
                    start_time TIMESTAMPTZ NOT NULL,
                    end_time TIMESTAMPTZ NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vc_reservations_channel_time ON vc_reservations (channel_id, start_time, end_time) WHERE status = 'active'")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vc_reservations_user ON vc_reservations (user_id, start_time) WHERE status = 'active'")
        print("[db] PostgreSQL ready (vc_logs, vc_lock_channels, vc_lock_members, join_requests, bot_settings, birthdays, birthday_nudges, points, vc_sessions, vc_reservations).", flush=True)
        return True
    except Exception as e:
        print(f"[db] Failed to connect/initialize PostgreSQL: {e}. Falling back to in-memory only.", flush=True)
        _pool = None
        return False


def is_enabled():
    return _pool is not None


@contextmanager
def _cursor():
    conn = _pool.getconn()
    broken = False
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except (psycopg2.InterfaceError, psycopg2.OperationalError):
        # Connection died server-side (idle timeout, network blip, etc).
        # Don't try to rollback a dead connection — just mark it for disposal
        # so the pool doesn't hand this same broken connection out again.
        broken = True
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        if broken:
            _pool.putconn(conn, close=True)
        else:
            _pool.putconn(conn)


# ---------- WhatsApp join requests ----------

_raw_join_channel_id = os.environ.get("OWNER_LOG_CHANNEL_ID", "0")
try:
    OWNER_LOG_CHANNEL_ID = int(_raw_join_channel_id)
except ValueError:
    print(f"[join] OWNER_LOG_CHANNEL_ID env var is not a valid integer: {_raw_join_channel_id!r}", flush=True)
    OWNER_LOG_CHANNEL_ID = 0

WHATSAPP_LINK_ENV_DEFAULT = os.environ.get("WHATSAPP_LINK", "")
if not WHATSAPP_LINK_ENV_DEFAULT:
    print("[join] WHATSAPP_LINK env var not set — set one, or use /set-whatsapp-link after startup.", flush=True)


# ---------- Feature toggles ----------
# Every optional feature checks one of these before doing anything, so you
# can flip a feature off instantly (dashboard or command) without a redeploy.

TOGGLE_KEYS = {
    "whatsapp": "WhatsApp invite system (join button, approvals, link updates)",
    "announcements": "DM-to-announce system",
    "birthday_new": "Birthday nudge for new members on join",
    "birthday_backfill": "Birthday nudge for existing members (retroactive)",
    "birthday_reminder_recurring": "Repeat birthday reminders on an interval (off = nudge once, never again)",
    "gambling": "Points/gambling system (daily claim, coinflip, slots, etc.)",
}
DEFAULT_TOGGLE_STATE = True  # every feature starts ON unless explicitly turned off


def get_toggle(key: str) -> bool:
    with _cursor() as cur:
        cur.execute("SELECT value FROM bot_settings WHERE key = %s", (f"toggle_{key}",))
        row = cur.fetchone()
        if row is None:
            return DEFAULT_TOGGLE_STATE
        return row["value"] == "on"


def set_toggle(key: str, enabled: bool):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (f"toggle_{key}", "on" if enabled else "off"),
        )


# ---------- Birthdays ----------

BIRTHDAY_WISH_MESSAGES = [
    "Happy Birthday, {mention}! Hope it's a great one \U0001F389",
    "It's {mention}'s birthday today \U0001F382 Go wish them well!",
    "\U0001F382 Everyone say happy birthday to {mention}!",
]


def get_birthday_channel_id() -> int:
    with _cursor() as cur:
        cur.execute("SELECT value FROM bot_settings WHERE key = 'birthday_channel_id'")
        row = cur.fetchone()
        return int(row["value"]) if row else 0


def set_birthday_channel_id(channel_id: int):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('birthday_channel_id', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(channel_id),),
        )


def has_birthday_registered(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("SELECT 1 FROM birthdays WHERE user_id = %s", (str(user_id),))
        return cur.fetchone() is not None


class BirthdayModal(discord.ui.Modal, title="Register your birthday"):
    day = discord.ui.TextInput(label="Day", placeholder="14", max_length=2)
    month = discord.ui.TextInput(label="Month", placeholder="3", max_length=2)
    year = discord.ui.TextInput(label="Year (optional)", placeholder="2004", required=False, max_length=4)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            day_val = int(str(self.day).strip())
            month_val = int(str(self.month).strip())
        except ValueError:
            await interaction.response.send_message("Day and month need to be numbers.", ephemeral=True)
            return

        if not (1 <= month_val <= 12) or not (1 <= day_val <= 31):
            await interaction.response.send_message("That's not a valid day/month.", ephemeral=True)
            return

        year_val = None
        year_str = str(self.year).strip()
        if year_str:
            try:
                year_val = int(year_str)
                if year_val < 1900 or year_val > datetime.now().year:
                    raise ValueError
            except ValueError:
                await interaction.response.send_message("That year doesn't look right.", ephemeral=True)
                return

        with _cursor() as cur:
            cur.execute(
                "INSERT INTO birthdays (user_id, day, month, year) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET day = EXCLUDED.day, month = EXCLUDED.month, year = EXCLUDED.year",
                (str(interaction.user.id), day_val, month_val, year_val),
            )

        await interaction.response.send_message(
            f"Saved \u2014 see you on {month_val}/{day_val} \U0001F382", ephemeral=True
        )


class BirthdayButtonView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.enter.custom_id = f"birthday_enter_{user_id}"

    @discord.ui.button(label="Enter your birthday", emoji="\U0001F382", style=discord.ButtonStyle.success)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BirthdayModal())


_raw_birthday_reminder_hours = os.environ.get("BIRTHDAY_REMINDER_INTERVAL_HOURS", "168")  # seed default: once a week
try:
    _BIRTHDAY_REMINDER_INTERVAL_DEFAULT = float(_raw_birthday_reminder_hours)
except ValueError:
    print(f"[birthday] BIRTHDAY_REMINDER_INTERVAL_HOURS env var is not a valid number: {_raw_birthday_reminder_hours!r}", flush=True)
    _BIRTHDAY_REMINDER_INTERVAL_DEFAULT = 168.0


def get_birthday_reminder_interval_hours() -> float:
    """Reads the current reminder interval from bot_settings, seeded from the
    env var on first run — same runtime-configurable pattern as the WhatsApp
    link, so this can be changed from /set-birthday-reminder-interval or the
    dashboard without a redeploy."""
    with _cursor() as cur:
        cur.execute("SELECT value FROM bot_settings WHERE key = 'birthday_reminder_interval_hours'")
        row = cur.fetchone()
        if row:
            return float(row["value"])
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('birthday_reminder_interval_hours', %s) "
            "ON CONFLICT (key) DO NOTHING",
            (str(_BIRTHDAY_REMINDER_INTERVAL_DEFAULT),),
        )
        return _BIRTHDAY_REMINDER_INTERVAL_DEFAULT


def set_birthday_reminder_interval_hours(hours: float):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('birthday_reminder_interval_hours', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(hours),),
        )


def get_last_birthday_nudge(user_id: int):
    with _cursor() as cur:
        cur.execute("SELECT last_nudged_at FROM birthday_nudges WHERE user_id = %s", (str(user_id),))
        row = cur.fetchone()
        return row["last_nudged_at"] if row else None


def record_birthday_nudge(user_id: int, at=None):
    at = at or datetime.now(timezone.utc)
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO birthday_nudges (user_id, last_nudged_at) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET last_nudged_at = EXCLUDED.last_nudged_at",
            (str(user_id), at),
        )


async def restore_birthday_views():
    """Re-registers 'Enter your birthday' buttons for anyone who hasn't
    filled it in yet, so the DM button keeps working after a restart."""
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    count = 0
    for member in guild.members:
        if member.bot or has_birthday_registered(member.id):
            continue
        bot.add_view(BirthdayButtonView(member.id))
        count += 1
    if count:
        print(f"[birthday] Restored {count} pending birthday-entry view(s).", flush=True)


async def nudge_member_for_birthday(member: discord.Member, force: bool = False) -> bool:
    """
    Sends the 'Enter your birthday' DM, gated by the birthday_reminder_recurring
    toggle:
      - ON  (default): resend after get_birthday_reminder_interval_hours()
        has elapsed since the last nudge — same repeat-safe fix as before,
        just with a runtime-configurable interval instead of a fixed env var.
      - OFF: send exactly ONCE, ever — if there's already a nudge on record
        at all, never nudge again regardless of how much time has passed.

    State lives in the birthday_nudges table, not memory, so it survives
    restarts/redeploys correctly either way. `force=True` (used for a
    genuine new-member join) always sends — a fresh join is a real welcome
    moment, not a reminder, so it isn't subject to either the interval or
    the once-only rule.

    Returns True if a DM was actually sent, False if skipped or failed.
    """
    if not force:
        last = get_last_birthday_nudge(member.id)
        if last is not None:
            if not get_toggle("birthday_reminder_recurring"):
                return False  # once-only mode — already nudged before, never again
            hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_since < get_birthday_reminder_interval_hours():
                return False  # nudged recently enough — don't spam

    try:
        await member.send(
            "Welcome! Before you dive in, drop your birthday so the squad can celebrate with you \U0001F382",
            view=BirthdayButtonView(member.id),
        )
        record_birthday_nudge(member.id)
        return True
    except discord.Forbidden:
        return False  # DMs closed — nothing more we can do


async def backfill_birthday_nudges():
    """
    Runs on every startup, but only actually DMs members who are due a
    nudge per nudge_member_for_birthday()'s rules (interval elapsed, or
    once-only mode hasn't sent theirs yet) — not the whole pending list
    every single time the bot reconnects.
    """
    if not get_toggle("birthday_backfill"):
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    sent = 0
    for member in guild.members:
        if member.bot or has_birthday_registered(member.id):
            continue
        if await nudge_member_for_birthday(member):
            sent += 1
        await asyncio.sleep(1)  # gentle pacing to avoid DM rate limits on larger servers
    if sent:
        mode = "recurring" if get_toggle("birthday_reminder_recurring") else "once-only"
        print(f"[birthday] Backfill-nudged {sent} member(s) (mode: {mode}).", flush=True)
    else:
        print("[birthday] Backfill sweep ran — no one needed a nudge right now.", flush=True)


async def daily_birthday_check_loop():
    """Runs once a day. Checks whose day+month matches today and posts a
    wish, using TIMEZONE_OFFSET_HOURS so 'today' matches your local day."""
    await bot.wait_until_ready()
    while not bot.is_closed():
        now_local = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET_HOURS)
        try:
            channel_id = get_birthday_channel_id()
            if channel_id:
                channel = bot.get_channel(channel_id)
                if channel:
                    with _cursor() as cur:
                        cur.execute(
                            "SELECT user_id, year, last_wished_year FROM birthdays WHERE day = %s AND month = %s",
                            (now_local.day, now_local.month),
                        )
                        rows = cur.fetchall()
                    for row in rows:
                        if row["last_wished_year"] == now_local.year:
                            continue  # already wished this year — restart-safe
                        member = bot.get_user(int(row["user_id"]))
                        if member is None:
                            continue
                        text = random.choice(BIRTHDAY_WISH_MESSAGES).format(mention=member.mention)
                        try:
                            await channel.send(
                                content=f"@everyone\n{text}",
                                allowed_mentions=discord.AllowedMentions(everyone=True, users=True),
                            )
                        except discord.Forbidden:
                            pass
                        with _cursor() as cur:
                            cur.execute(
                                "UPDATE birthdays SET last_wished_year = %s WHERE user_id = %s",
                                (now_local.year, row["user_id"]),
                            )
        except Exception as e:
            print(f"[birthday] Daily check failed (non-fatal): {e}", flush=True)

        await asyncio.sleep(3600)  # re-check hourly — catches the right local day even across restarts


def get_whatsapp_link() -> str:
    """Reads the current invite link from the DB, seeding it from the env
    var on first run. This makes the link updatable at runtime via
    /set-whatsapp-link without needing a redeploy."""
    with _cursor() as cur:
        cur.execute("SELECT value FROM bot_settings WHERE key = 'whatsapp_link'")
        row = cur.fetchone()
        if row:
            return row["value"]
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('whatsapp_link', %s) "
            "ON CONFLICT (key) DO NOTHING",
            (WHATSAPP_LINK_ENV_DEFAULT,),
        )
        return WHATSAPP_LINK_ENV_DEFAULT


def set_whatsapp_link(new_link: str):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('whatsapp_link', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (new_link,),
        )

JOIN_DENIED_COOLDOWN_SECONDS = 24 * 60 * 60      # wait 24h after a decline before retrying
JOIN_MIN_ACCOUNT_AGE_SECONDS = 7 * 24 * 60 * 60  # ignore brand-new Discord accounts (raid bots)


class JoinModal(discord.ui.Modal, title="Join WhatsApp squad"):
    name = discord.ui.TextInput(label="Name / IGN", placeholder="Malak#1234", max_length=32)
    phone = discord.ui.TextInput(label="Phone number", placeholder="+91 90000 00000", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        if not get_toggle("whatsapp"):
            await interaction.response.send_message("WhatsApp join requests are turned off right now.", ephemeral=True)
            return
        if not is_enabled():
            await interaction.response.send_message(
                "Join requests aren't available right now — database isn't connected.", ephemeral=True
            )
            return

        account_age = time.time() - interaction.user.created_at.timestamp()
        if account_age < JOIN_MIN_ACCOUNT_AGE_SECONDS:
            await interaction.response.send_message(
                "Your account's too new to request this yet.", ephemeral=True
            )
            return

        with _cursor() as cur:
            cur.execute(
                "SELECT status, EXTRACT(EPOCH FROM created_at) AS created_at "
                "FROM join_requests WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                (str(interaction.user.id),),
            )
            row = cur.fetchone()

        if row:
            if row["status"] == "pending":
                await interaction.response.send_message(
                    "You already have a request pending review.", ephemeral=True
                )
                return
            if row["status"] == "approved":
                await interaction.response.send_message(
                    "You're already approved — check your DMs for the link.", ephemeral=True
                )
                return
            if row["status"] == "denied" and time.time() - row["created_at"] < JOIN_DENIED_COOLDOWN_SECONDS:
                hours_left = int((JOIN_DENIED_COOLDOWN_SECONDS - (time.time() - row["created_at"])) / 3600)
                await interaction.response.send_message(
                    f"You can try again in about {hours_left}h.", ephemeral=True
                )
                return

        with _cursor() as cur:
            cur.execute(
                "INSERT INTO join_requests (user_id, name, phone, status) VALUES (%s, %s, %s, 'pending') RETURNING id",
                (str(interaction.user.id), str(self.name), str(self.phone)),
            )
            req_id = cur.fetchone()["id"]

        await interaction.response.send_message(
            "Request sent. You'll get a DM once it's reviewed.", ephemeral=True
        )

        channel = bot.get_channel(OWNER_LOG_CHANNEL_ID) if bot else None
        if channel is None:
            print(f"[join] Could not find OWNER_LOG_CHANNEL_ID={OWNER_LOG_CHANNEL_ID} to post request #{req_id}.", flush=True)
            return

        embed = discord.Embed(title="New join request", color=discord.Color.blurple())
        embed.add_field(name="Discord", value=interaction.user.mention, inline=True)
        embed.add_field(name="Name / IGN", value=str(self.name), inline=True)
        embed.add_field(name="Phone", value=str(self.phone), inline=False)
        embed.set_footer(text=f"Request #{req_id}")
        await channel.send(embed=embed, view=JoinApprovalView(req_id, interaction.user.id))


class JoinApprovalView(discord.ui.View):
    """Approve/Hold/Decline buttons with no timeout — sit here until acted on, even across restarts."""

    def __init__(self, req_id: int, user_id: int):
        super().__init__(timeout=None)
        self.req_id = req_id
        self.user_id = user_id
        self.approve.custom_id = f"join_approve_{req_id}"
        self.hold.custom_id = f"join_hold_{req_id}"
        self.decline.custom_id = f"join_decline_{req_id}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Backup check — even if this message somehow becomes visible to a
        # non-admin (wrong channel permissions, etc.), only real admins can
        # actually click through to approve/decline/hold.
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
            await interaction.response.send_message(
                "Only server admins can act on join requests.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "approved")

    @discord.ui.button(label="Hold", style=discord.ButtonStyle.secondary)
    async def hold(self, interaction: discord.Interaction, button: discord.ui.Button):
        with _cursor() as cur:
            cur.execute(
                "UPDATE join_requests SET status = 'on_hold', resolved_by_id = %s, "
                "resolved_by_name = %s, resolved_at = NOW() WHERE id = %s",
                (str(interaction.user.id), str(interaction.user.display_name), self.req_id),
            )

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.light_grey()
        embed.set_footer(text=f"Request #{self.req_id} — on hold by {interaction.user.display_name}")
        # Approve/Hold/Decline stay active — you can still act on this whenever you're ready
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, "denied")

    async def _resolve(self, interaction: discord.Interaction, status: str):
        with _cursor() as cur:
            cur.execute(
                "UPDATE join_requests SET status = %s, resolved_by_id = %s, "
                "resolved_by_name = %s, resolved_at = NOW() WHERE id = %s",
                (status, str(interaction.user.id), str(interaction.user.display_name), self.req_id),
            )

        for child in self.children:
            child.disabled = True
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green() if status == "approved" else discord.Color.red()
        embed.set_footer(text=f"Request #{self.req_id} — {status} by {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed, view=self)

        try:
            member = bot.get_user(self.user_id) or await bot.fetch_user(self.user_id)
            if status == "approved":
                await member.send(
                    f"You're approved! Join here: {get_whatsapp_link()}",
                    view=LinkExpiredReportView(self.user_id),
                )
            else:
                await member.send("Your join request wasn't approved this time.")
        except discord.Forbidden:
            pass  # user has DMs closed — nothing more we can do


class ToggleFeatureSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=desc, value=key, emoji=("\u2705" if get_toggle(key) else "\u26AB"))
            for key, desc in TOGGLE_KEYS.items()
        ]
        super().__init__(placeholder="Select a feature...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        current = get_toggle(key)
        state_text = "ON \u2705" if current else "OFF \u26AB"
        await interaction.response.edit_message(
            content=f"**{TOGGLE_KEYS[key]}**\nCurrently: {state_text}",
            view=ToggleOnOffView(key),
        )


class ToggleFeatureSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ToggleFeatureSelect())


class ToggleOnOffView(discord.ui.View):
    def __init__(self, key: str):
        super().__init__(timeout=120)
        self.key = key

    @discord.ui.button(label="Turn ON", style=discord.ButtonStyle.success, emoji="\u2705")
    async def turn_on(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_toggle(self.key, True)
        await interaction.response.edit_message(content=f"**{TOGGLE_KEYS[self.key]}**\nNow: ON \u2705", view=None)

    @discord.ui.button(label="Turn OFF", style=discord.ButtonStyle.danger, emoji="\u26AB")
    async def turn_off(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_toggle(self.key, False)
        await interaction.response.edit_message(content=f"**{TOGGLE_KEYS[self.key]}**\nNow: OFF \u26AB", view=None)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Pick a feature to toggle:", view=ToggleFeatureSelectView())


class LinkExpiredReportView(discord.ui.View):
    """Sits on the 'You're approved!' DM. Lets that specific member flag a dead
    link without you having to notice on your own."""

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.report.custom_id = f"link_expired_report_{user_id}"

    @discord.ui.button(label="Link expired?", style=discord.ButtonStyle.secondary, emoji="\u26A0\uFE0F")
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        button.label = "Reported"
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("Reported — an admin will send you a fresh link shortly.", ephemeral=True)

        for admin_id in ANNOUNCE_ADMIN_IDS:
            try:
                admin = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
                await admin.send(
                    f"\u26A0\uFE0F **{interaction.user}** says the WhatsApp link expired.",
                    view=AdminSetLinkView(self.user_id),
                )
            except (discord.Forbidden, discord.NotFound, ValueError):
                pass  # admin has DMs closed, invalid ID, etc. — skip, don't crash the report


class AdminSetLinkView(discord.ui.View):
    """DMed to admins when a member reports the link as dead. Only sends the
    fresh link back to that ONE reporting member, not everyone approved."""

    def __init__(self, reporting_user_id: int):
        super().__init__(timeout=None)
        self.reporting_user_id = reporting_user_id

    @discord.ui.button(label="Set new link", style=discord.ButtonStyle.primary, emoji="\U0001F517")
    async def set_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetNewLinkModal(self.reporting_user_id, interaction.message))

    @discord.ui.button(label="Cancel report", style=discord.ButtonStyle.secondary, emoji="\u2716\uFE0F")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DismissReportModal(self.reporting_user_id, interaction.message))


async def _disable_admin_report_message(origin_message: discord.Message, reporting_user_id: int, note: str):
    """Greys out the Set new link / Cancel report buttons on the admin's DM
    once it's been actioned, so it can't be double-clicked."""
    if origin_message is None:
        return
    disabled_view = AdminSetLinkView(reporting_user_id)
    for child in disabled_view.children:
        child.disabled = True
    try:
        await origin_message.edit(content=f"{origin_message.content}\n\n{note}", view=disabled_view)
    except discord.HTTPException:
        pass  # message may have been deleted — not critical


class SetNewLinkModal(discord.ui.Modal, title="Set new WhatsApp link"):
    new_link = discord.ui.TextInput(label="New invite link", placeholder="https://chat.whatsapp.com/...")

    def __init__(self, reporting_user_id: int, origin_message: discord.Message = None):
        super().__init__()
        self.reporting_user_id = reporting_user_id
        self.origin_message = origin_message
        # Pre-fill with whatever's currently saved — if this is the 2nd/3rd report
        # for the same expiry, you can just confirm instead of retyping it.
        current = get_whatsapp_link()
        if current:
            self.new_link.default = current

    async def on_submit(self, interaction: discord.Interaction):
        link = str(self.new_link).strip()
        if not link.startswith("https://chat.whatsapp.com/"):
            await interaction.response.send_message(
                "That doesn't look like a WhatsApp invite link — expected it to start with "
                "https://chat.whatsapp.com/", ephemeral=True
            )
            return

        set_whatsapp_link(link)
        await interaction.response.send_message("Link updated and sent to the member who reported it.", ephemeral=True)
        await _disable_admin_report_message(self.origin_message, self.reporting_user_id, "\u2705 Handled — new link sent.")

        try:
            member = bot.get_user(self.reporting_user_id) or await bot.fetch_user(self.reporting_user_id)
            await member.send(f"Here's the fresh link: {link}")
        except discord.Forbidden:
            pass  # they have DMs closed — nothing more we can do


class DismissReportModal(discord.ui.Modal, title="Cancel this report"):
    message_to_member = discord.ui.TextInput(
        label="Message to send them (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Link's working fine on my end — try again in a bit.",
        required=False,
        max_length=300,
    )

    def __init__(self, reporting_user_id: int, origin_message: discord.Message = None):
        super().__init__()
        self.reporting_user_id = reporting_user_id
        self.origin_message = origin_message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Report cancelled.", ephemeral=True)
        await _disable_admin_report_message(self.origin_message, self.reporting_user_id, "\u2716\uFE0F Cancelled.")

        text = str(self.message_to_member).strip()
        if not text:
            return  # admin chose not to send anything back
        try:
            member = bot.get_user(self.reporting_user_id) or await bot.fetch_user(self.reporting_user_id)
            await member.send(text)
        except discord.Forbidden:
            pass


# ---------- Points (gambling foundation) ----------

DAILY_CLAIM_AMOUNT = 100


def get_balance(user_id: int) -> int:
    with _cursor() as cur:
        cur.execute("SELECT balance FROM points WHERE user_id = %s", (str(user_id),))
        row = cur.fetchone()
        return row["balance"] if row else 0


def add_balance(user_id: int, amount: int):
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO points (user_id, balance) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = points.balance + %s",
            (str(user_id), amount, amount),
        )


class DailyClaimView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(label="Claim", emoji="\U0001F381", style=discord.ButtonStyle.success)
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your claim button.", ephemeral=True)
            return

        today = datetime.now(timezone.utc).date()
        with _cursor() as cur:
            cur.execute("SELECT last_daily_claim FROM points WHERE user_id = %s", (str(self.user_id),))
            row = cur.fetchone()
        if row and row["last_daily_claim"] == today:
            await interaction.response.send_message("Already claimed today \u2014 come back tomorrow.", ephemeral=True)
            return

        add_balance(self.user_id, DAILY_CLAIM_AMOUNT)
        with _cursor() as cur:
            cur.execute(
                "UPDATE points SET last_daily_claim = %s WHERE user_id = %s", (today, str(self.user_id))
            )
        button.disabled = True
        button.label = "Claimed"
        new_balance = get_balance(self.user_id)
        await interaction.response.edit_message(
            content=f"+{DAILY_CLAIM_AMOUNT} points! Balance: {new_balance}", view=self
        )


class CoinflipAcceptView(discord.ui.View):
    def __init__(self, challenger_id: int, amount: int):
        super().__init__(timeout=300)
        self.challenger_id = challenger_id
        self.amount = amount
        self.resolved = False

    @discord.ui.button(label="Accept Challenge", emoji="\U0001FA99", style=discord.ButtonStyle.danger)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            await interaction.response.send_message("This challenge is already settled.", ephemeral=True)
            return
        if interaction.user.id == self.challenger_id:
            await interaction.response.send_message("You can't accept your own challenge.", ephemeral=True)
            return
        if get_balance(interaction.user.id) < self.amount:
            await interaction.response.send_message(f"You need {self.amount} points to accept this.", ephemeral=True)
            return
        challenger_balance = get_balance(self.challenger_id)
        if challenger_balance < self.amount:
            await interaction.response.send_message("The challenger no longer has enough points.", ephemeral=True)
            return

        self.resolved = True
        button.disabled = True

        winner_id = random.choice([self.challenger_id, interaction.user.id])
        loser_id = interaction.user.id if winner_id == self.challenger_id else self.challenger_id
        add_balance(winner_id, self.amount)
        add_balance(loser_id, -self.amount)

        winner = bot.get_user(winner_id) or await bot.fetch_user(winner_id)
        loser = bot.get_user(loser_id) or await bot.fetch_user(loser_id)
        await interaction.response.edit_message(
            content=(
                f"\U0001FA99 **Coinflip settled!**\n"
                f"{winner.mention} wins **{self.amount}** points off {loser.mention}!"
            ),
            view=self,
        )


class SlotsSpinView(discord.ui.View):
    SYMBOLS = ["\U0001F352", "\U0001F34B", "\U0001F514", "\U0001F48E", "7\uFE0F\u20E3"]

    def __init__(self, user_id: int, amount: int):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.amount = amount

    @discord.ui.button(label="Spin again", emoji="\U0001F3B0", style=discord.ButtonStyle.primary)
    async def spin_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your machine — start your own with `/slots`.", ephemeral=True)
            return
        if get_balance(self.user_id) < self.amount:
            await interaction.response.send_message(f"You need {self.amount} points to spin again.", ephemeral=True)
            return
        content, _ = run_slots_spin(self.user_id, self.amount)
        await interaction.response.edit_message(content=content, view=self)


def run_slots_spin(user_id: int, amount: int) -> tuple:
    add_balance(user_id, -amount)
    reels = [random.choice(SlotsSpinView.SYMBOLS) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        payout = amount * 5
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        payout = amount * 2
    else:
        payout = 0
    if payout:
        add_balance(user_id, payout)
    new_balance = get_balance(user_id)
    row = " ".join(reels)
    if payout > amount:
        result = f"\U0001F389 **{row}** \u2014 won {payout}! Balance: {new_balance}"
    elif payout:
        result = f"**{row}** \u2014 pushed, got {payout} back. Balance: {new_balance}"
    else:
        result = f"**{row}** \u2014 no match. Balance: {new_balance}"
    return f"<@{user_id}>\n{result}", payout


class DiceHighLowView(discord.ui.View):
    def __init__(self, user_id: int, amount: int, current: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.amount = amount
        self.current = current

    async def _resolve(self, interaction: discord.Interaction, guess_higher: bool):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your roll — start your own with `/dice`.", ephemeral=True)
            return
        next_roll = random.randint(1, 6)
        won = (next_roll > self.current) if guess_higher else (next_roll < self.current)
        if next_roll == self.current:
            won = False  # a tie never counts as a correct guess, in either direction
        for child in self.children:
            child.disabled = True
        if won:
            add_balance(self.user_id, self.amount)
            text = f"Rolled **{next_roll}** \u2014 correct! +{self.amount}. Balance: {get_balance(self.user_id)}"
        else:
            add_balance(self.user_id, -self.amount)
            text = f"Rolled **{next_roll}** \u2014 wrong. \u2212{self.amount}. Balance: {get_balance(self.user_id)}"
        await interaction.response.edit_message(content=f"<@{self.user_id}>\n{text}", view=self)

    @discord.ui.button(label="Higher", emoji="\u2B06\uFE0F", style=discord.ButtonStyle.success)
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, True)

    @discord.ui.button(label="Lower", emoji="\u2B07\uFE0F", style=discord.ButtonStyle.danger)
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._resolve(interaction, False)


async def restore_link_expired_views():
    """Re-registers the 'Link expired?' button on every approved member's DM
    so it keeps working after a bot restart, same idea as restore_join_views()."""
    with _cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM join_requests WHERE status = 'approved'")
        rows = cur.fetchall()
    for row in rows:
        bot.add_view(LinkExpiredReportView(int(row["user_id"])))
    if rows:
        print(f"[join] Restored {len(rows)} link-expired report view(s).", flush=True)


class JoinButtonView(discord.ui.View):
    """The permanent 'Join WhatsApp Squad' button posted in a public channel.
    Clicking it opens the same modal /join-whatsapp used to open."""

    def __init__(self):
        super().__init__(timeout=None)
        self.join.custom_id = "join_whatsapp_button"

    @discord.ui.button(label="Join WhatsApp Squad", emoji="\U0001F4F2", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not get_toggle("whatsapp"):
            await interaction.response.send_message("WhatsApp join requests are turned off right now.", ephemeral=True)
            return
        await interaction.response.send_modal(JoinModal())


_raw_join_button_channel_id = os.environ.get("JOIN_BUTTON_CHANNEL_ID", "0")
try:
    JOIN_BUTTON_CHANNEL_ID = int(_raw_join_button_channel_id)
except ValueError:
    print(f"[join] JOIN_BUTTON_CHANNEL_ID env var is not a valid integer: {_raw_join_button_channel_id!r}", flush=True)
    JOIN_BUTTON_CHANNEL_ID = 0


async def ensure_join_button_posted():
    """Posts the permanent join-button message once, if it isn't already
    sitting in the channel (checked on every startup so it's never duplicated)."""
    if not JOIN_BUTTON_CHANNEL_ID:
        return
    channel = bot.get_channel(JOIN_BUTTON_CHANNEL_ID)
    if channel is None:
        print(f"[join] Could not find JOIN_BUTTON_CHANNEL_ID={JOIN_BUTTON_CHANNEL_ID}.", flush=True)
        return
    async for msg in channel.history(limit=50):
        if msg.author.id == bot.user.id and msg.embeds and msg.embeds[0].title == "Join WhatsApp Squad":
            return  # already posted — don't duplicate it
    embed = discord.Embed(
        title="Join WhatsApp Squad",
        description="Tap the button below to request an invite. Your name and number are only visible to admins reviewing the request.",
        color=discord.Color.green(),
    )
    await channel.send(content="@everyone", embed=embed, view=JoinButtonView(),
                        allowed_mentions=discord.AllowedMentions(everyone=True))


async def restore_join_views():
    """Re-registers Approve/Decline buttons on still-pending requests so they
    keep working after a bot restart. Call once from on_ready."""
    if not is_enabled():
        return
    with _cursor() as cur:
        cur.execute("SELECT id, user_id FROM join_requests WHERE status = 'pending'")
        rows = cur.fetchall()
    for row in rows:
        bot.add_view(JoinApprovalView(row["id"], int(row["user_id"])))
    if rows:
        print(f"[join] Restored {len(rows)} pending approval view(s).", flush=True)


# ---------- Logs ----------

def log_event(action, user_id=None, user_name=None, actor_id=None, actor_name=None,
              from_channel_id=None, from_channel_name=None,
              to_channel_id=None, to_channel_name=None, details=None):
    if not _pool:
        return
    sql = """
        INSERT INTO vc_logs
            (action, user_id, user_name, actor_id, actor_name,
             from_channel_id, from_channel_name, to_channel_id, to_channel_name, details)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (action, user_id, user_name, actor_id, actor_name,
              from_channel_id, from_channel_name, to_channel_id, to_channel_name, details)
    for attempt in range(2):  # one retry — the first attempt may hit a stale pooled connection
        try:
            with _cursor() as cur:
                cur.execute(sql, params)
            return
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            if attempt == 0:
                continue  # _cursor() already discarded the dead connection — retry with a fresh one
            print(f"[db] log_event failed after retry: {e}")
        except Exception as e:
            print(f"[db] log_event failed: {e}")
            return


def fetch_logs(limit=100):
    if not _pool:
        return []
    try:
        with _cursor() as cur:
            cur.execute("SELECT * FROM vc_logs ORDER BY created_at DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
            return [
                {**r, "created_at": r["created_at"].isoformat() if r["created_at"] else None}
                for r in rows
            ]
    except Exception as e:
        print(f"[db] fetch_logs failed: {e}")
        return []


# ---------- Locks ----------

def save_lock_channel(channel_id, channel_name):
    if not _pool:
        return
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO vc_lock_channels (channel_id, channel_name) VALUES (%s, %s)
                ON CONFLICT (channel_id) DO UPDATE SET channel_name = EXCLUDED.channel_name
            """, (str(channel_id), channel_name))
    except Exception as e:
        print(f"[db] save_lock_channel failed: {e}")


def remove_lock_channels(channel_ids):
    if not _pool or not channel_ids:
        return
    try:
        with _cursor() as cur:
            cur.execute(
                "DELETE FROM vc_lock_channels WHERE channel_id = ANY(%s)",
                ([str(c) for c in channel_ids],)
            )
    except Exception as e:
        print(f"[db] remove_lock_channels failed: {e}")


def clear_lock_channels():
    if not _pool:
        return
    try:
        with _cursor() as cur:
            cur.execute("DELETE FROM vc_lock_channels")
    except Exception as e:
        print(f"[db] clear_lock_channels failed: {e}")


def save_lock_member(user_id, allowed_channel_id):
    if not _pool:
        return
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO vc_lock_members (user_id, allowed_channel_id) VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET allowed_channel_id = EXCLUDED.allowed_channel_id, locked_at = NOW()
            """, (str(user_id), str(allowed_channel_id)))
    except Exception as e:
        print(f"[db] save_lock_member failed: {e}")


def remove_lock_members(user_ids):
    if not _pool or not user_ids:
        return
    try:
        with _cursor() as cur:
            cur.execute(
                "DELETE FROM vc_lock_members WHERE user_id = ANY(%s)",
                ([str(u) for u in user_ids],)
            )
    except Exception as e:
        print(f"[db] remove_lock_members failed: {e}")


def remove_lock_members_by_channels(channel_ids):
    if not _pool or not channel_ids:
        return
    try:
        with _cursor() as cur:
            cur.execute(
                "DELETE FROM vc_lock_members WHERE allowed_channel_id = ANY(%s)",
                ([str(c) for c in channel_ids],)
            )
    except Exception as e:
        print(f"[db] remove_lock_members_by_channels failed: {e}")


def clear_lock_members():
    if not _pool:
        return
    try:
        with _cursor() as cur:
            cur.execute("DELETE FROM vc_lock_members")
    except Exception as e:
        print(f"[db] clear_lock_members failed: {e}")


# ---------- Analytics ----------

def fetch_events_since(days_back=90, limit=20000):
    """
    Raw log events ordered ASCENDING by time, for the analytics module to turn
    into VC sessions. Capped to the last `days_back` days / `limit` rows —
    fine for a friends server, but a known limit worth knowing: leaderboards
    and "all time" stats only look back this far, not truly forever.
    """
    if not _pool:
        return []
    try:
        with _cursor() as cur:
            cur.execute("""
                SELECT action, user_id, user_name, from_channel_name, to_channel_name, created_at
                FROM vc_logs
                WHERE created_at >= NOW() - (%s || ' days')::interval
                ORDER BY created_at ASC
                LIMIT %s
            """, (days_back, limit))
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"[db] fetch_events_since failed: {e}")
        return []


def load_locks():
    """Returns (set of locked channel_ids as int, dict user_id(int) -> allowed_channel_id(int))."""
    if not _pool:
        return set(), {}
    try:
        with _cursor() as cur:
            cur.execute("SELECT channel_id FROM vc_lock_channels")
            channel_ids = {int(r["channel_id"]) for r in cur.fetchall()}
            cur.execute("SELECT user_id, allowed_channel_id FROM vc_lock_members")
            members = {int(r["user_id"]): int(r["allowed_channel_id"]) for r in cur.fetchall()}
            return channel_ids, members
    except Exception as e:
        print(f"[db] load_locks failed: {e}")
        return set(), {}



# ============================================================
# VC SESSIONS (interpreted layer — see vc_stats.py for the pure math)
# ============================================================
# vc_logs stays exactly as before: the raw, permanent audit trail of
# whatever Discord reported to us — nothing about it changes. vc_sessions
# is a new INTERPRETED layer on top of it: one row per continuous stay in
# a channel, with a real "still open" concept (ended_at IS NULL) instead
# of re-deriving every session from scratch by replaying raw events on
# every single dashboard load.
#
# Every write below is atomic within one _cursor() transaction (close any
# existing open row + insert the new one, or just close the existing open
# row, in a single round trip) so there's never a window where two open
# rows can exist for the same user. A partial unique index (see init_db)
# enforces this as a hard DB-level invariant as a second line of defense.

def open_or_move_session(user_id, user_name, channel_id, channel_name, at=None, start_is_estimated=False):
    """Opens a new session for this user, closing any existing open one first
    (same transaction) — this is what makes MOVE just work: close the old
    channel's session, open the new channel's session, atomically. Used for
    both JOINED and MOVED."""
    if not _pool:
        return
    at = at or datetime.now(timezone.utc)
    for attempt in range(2):
        try:
            with _cursor() as cur:
                cur.execute("""
                    UPDATE vc_sessions
                    SET ended_at = %s, duration_seconds = EXTRACT(EPOCH FROM (%s - started_at))
                    WHERE user_id = %s AND ended_at IS NULL
                """, (at, at, str(user_id)))
                cur.execute("""
                    INSERT INTO vc_sessions
                        (user_id, user_name, channel_id, channel_name, started_at, start_is_estimated)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (str(user_id), user_name, str(channel_id) if channel_id else None,
                      channel_name, at, start_is_estimated))
            return
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            if attempt == 0:
                continue
            print(f"[vc_sessions] open_or_move_session failed after retry for user {user_id}", flush=True)
        except psycopg2.errors.UniqueViolation:
            print(
                f"[vc_sessions] Unexpected duplicate-open race for user {user_id} — skipped, "
                f"will self-heal on next reconciliation.", flush=True,
            )
            return
        except Exception as e:
            print(f"[vc_sessions] open_or_move_session failed: {e}", flush=True)
            return


def close_session(user_id, at=None, end_is_estimated=False):
    """Closes this user's open session, if any. Idempotent — a no-op if there isn't one."""
    if not _pool:
        return
    at = at or datetime.now(timezone.utc)
    for attempt in range(2):
        try:
            with _cursor() as cur:
                cur.execute("""
                    UPDATE vc_sessions
                    SET ended_at = %s,
                        duration_seconds = EXTRACT(EPOCH FROM (%s - started_at)),
                        end_is_estimated = %s
                    WHERE user_id = %s AND ended_at IS NULL
                """, (at, at, end_is_estimated, str(user_id)))
            return
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            if attempt == 0:
                continue
            print(f"[vc_sessions] close_session failed after retry for user {user_id}", flush=True)
        except Exception as e:
            print(f"[vc_sessions] close_session failed: {e}", flush=True)
            return


def fetch_sessions_for_period(period_start, period_end):
    """
    Pulls every vc_sessions row that OVERLAPS [period_start, period_end)
    straight from the database — no row/day cap. period_start=None means
    no lower bound at all (the "all" period): genuinely every session ever
    recorded, not a rolling window.

    Returns plain dicts shaped exactly like vc_stats' session dicts, ready
    to pass straight into vc_stats.clip_sessions_to_period().

    Retries once on a dead pooled connection (same pattern as log_event) —
    without this, a single stale connection meant one dashboard load would
    silently come back with empty stats instead of the real numbers.
    """
    if not _pool:
        return []
    for attempt in range(2):
        try:
            with _cursor() as cur:
                if period_start is None:
                    cur.execute("""
                        SELECT user_id, user_name, channel_name, started_at, ended_at
                        FROM vc_sessions
                        ORDER BY started_at ASC
                    """)
                else:
                    cur.execute("""
                        SELECT user_id, user_name, channel_name, started_at, ended_at
                        FROM vc_sessions
                        WHERE started_at < %s AND COALESCE(ended_at, NOW()) >= %s
                        ORDER BY started_at ASC
                    """, (period_end, period_start))
                rows = cur.fetchall()
                return [
                    {
                        "user_id": r["user_id"], "user_name": r["user_name"], "channel_name": r["channel_name"],
                        "start": r["started_at"], "end": r["ended_at"],
                    }
                    for r in rows
                ]
        except (psycopg2.InterfaceError, psycopg2.OperationalError):
            if attempt == 0:
                continue  # _cursor() already discarded the dead connection — retry with a fresh one
            print("[vc_sessions] fetch_sessions_for_period failed after retry — returning empty.", flush=True)
            return []
        except Exception as e:
            print(f"[vc_sessions] fetch_sessions_for_period failed: {e}", flush=True)
            return []
    return []


def backfill_vc_sessions_from_logs():
    """
    One-time migration: replays the ENTIRE vc_logs history (no day/row cap —
    this is a one-off job, not a hot path) through the exact reconstruction
    logic in vc_stats.py, and writes the result into vc_sessions. Sessions
    still open at the end of the replay are left open (end=None) rather than
    force-closed at "now" — the reconciliation pass that runs right after
    the bot connects resolves each one against real presence, which is a
    far better source of truth than guessing when someone actually left.

    Gated by a bot_settings flag so this only ever runs once, no matter how
    many times the app redeploys/restarts afterward.
    """
    if not _pool:
        return
    with _cursor() as cur:
        cur.execute("SELECT value FROM bot_settings WHERE key = 'vc_sessions_backfilled'")
        if cur.fetchone():
            print("[vc_sessions] Backfill already completed previously — skipping.", flush=True)
            return

    with _cursor() as cur:
        cur.execute("""
            SELECT action, user_id, user_name, from_channel_name, to_channel_name, created_at
            FROM vc_logs
            ORDER BY created_at ASC
        """)
        events = [dict(r) for r in cur.fetchall()]

    def _mark_done():
        with _cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (key, value) VALUES ('vc_sessions_backfilled', %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (datetime.now(timezone.utc).isoformat(),),
            )

    if not events:
        print("[vc_sessions] No vc_logs history to backfill from.", flush=True)
        _mark_done()
        return

    sessions = vs.reconstruct_sessions_with_ids(events, close_open_at_now=False)

    with _cursor() as cur:
        for s in sessions:
            duration = (s["end"] - s["start"]).total_seconds() if s["end"] else None
            cur.execute("""
                INSERT INTO vc_sessions
                    (user_id, user_name, channel_name, started_at, ended_at, duration_seconds)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (s["user_id"], s["user_name"], s["channel_name"], s["start"], s["end"], duration))
    _mark_done()

    still_open = sum(1 for s in sessions if s["end"] is None)
    print(
        f"[vc_sessions] Backfill complete: {len(sessions)} session(s) created from {len(events)} raw "
        f"vc_logs events ({still_open} left open, pending reconciliation against real presence).",
        flush=True,
    )


async def reconcile_vc_sessions():
    """
    Runs once per bot connect/reconnect, alongside the existing (unchanged)
    reconcile_stale_sessions(). This is the NEW piece: reconciles
    vc_sessions against REAL Discord presence, in three ways:

      - closes any open vc_sessions row whose user is no longer actually
        present (bot missed their leave while offline)
      - OPENS a session for anyone actually present with no open row at all
        (bot missed their join while offline, or — the audit's Critical/
        High finding — they were already in voice the very first time the
        bot connected after backfill). The old system never did this
        direction at all.
      - closes + reopens (in the new channel) for anyone present but in a
        DIFFERENT channel than their open session says — i.e. they moved
        channels entirely while the bot was offline. Comparing only
        user_id (as an earlier version of this function did) would miss
        this: the user "is present" either way, so a user_id-only check
        would leave their session silently misattributed to the old,
        stale channel for the rest of its lifetime.

    The close/open decision itself is pure dict/set math, tested in
    isolation — see vc_stats.compute_reconciliation_actions. This function's
    only job is gathering the two real-world channel maps and applying the
    result.
    """
    if not _pool:
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    with _cursor() as cur:
        cur.execute("SELECT user_id, channel_id FROM vc_sessions WHERE ended_at IS NULL")
        open_sessions = {r["user_id"]: r["channel_id"] for r in cur.fetchall()}

    currently_present = {}       # user_id (str) -> channel_id (str), for the reconciliation comparison
    currently_present_info = {}  # user_id (str) -> (channel_id, channel_name), for opening new sessions
    for vc in guild.channels:
        if isinstance(vc, discord.VoiceChannel):
            for m in vc.members:
                currently_present[str(m.id)] = str(vc.id)
                currently_present_info[str(m.id)] = (vc.id, vc.name)

    to_close, to_open = vs.compute_reconciliation_actions(open_sessions, currently_present)

    now = datetime.now(timezone.utc)
    for uid in to_close:
        close_session(uid, at=now, end_is_estimated=True)
    for uid in to_open:
        channel_id, channel_name = currently_present_info[uid]
        member = guild.get_member(int(uid))
        user_name = member.display_name if member else "Unknown"
        open_or_move_session(uid, user_name, channel_id, channel_name, at=now, start_is_estimated=True)

    if to_close or to_open:
        print(
            f"[vc_sessions] Reconciled: closed {len(to_close)} stale open session(s), "
            f"opened {len(to_open)} for member(s) already present with no session on record.",
            flush=True,
        )


# ============================================================
# VC RESERVATIONS
# ============================================================
MAX_RESERVATION_MINUTES = 12 * 60  # sanity cap — prevents a mistyped duration from blocking a channel for days


def create_reservation(user_id, user_name, channel_id, channel_name, start_time, duration_minutes):
    """
    Inserts a reservation IF it doesn't conflict with an existing active one
    in the same channel. Returns (True, reservation_id) on success, or
    (False, conflicting_row_dict) if the slot is already taken — both the
    conflict check and the insert happen in one transaction so two people
    reserving the same slot at the same instant can't both succeed.
    """
    end_time = start_time + timedelta(minutes=duration_minutes)
    with _cursor() as cur:
        cur.execute("""
            SELECT id, user_name, start_time, end_time FROM vc_reservations
            WHERE channel_id = %s AND status = 'active'
              AND start_time < %s AND end_time > %s
            ORDER BY start_time ASC LIMIT 1
        """, (str(channel_id), end_time, start_time))
        conflict = cur.fetchone()
        if conflict:
            return False, dict(conflict)

        cur.execute("""
            INSERT INTO vc_reservations
                (user_id, user_name, channel_id, channel_name, start_time, end_time, duration_minutes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (str(user_id), user_name, str(channel_id), channel_name, start_time, end_time, duration_minutes))
        new_id = cur.fetchone()["id"]
    return True, new_id


def list_upcoming_reservations(user_id=None, limit=10):
    now = datetime.now(timezone.utc)
    with _cursor() as cur:
        if user_id:
            cur.execute("""
                SELECT id, user_id, user_name, channel_id, channel_name, start_time, end_time
                FROM vc_reservations
                WHERE status = 'active' AND end_time > %s AND user_id = %s
                ORDER BY start_time ASC LIMIT %s
            """, (now, str(user_id), limit))
        else:
            cur.execute("""
                SELECT id, user_id, user_name, channel_id, channel_name, start_time, end_time
                FROM vc_reservations
                WHERE status = 'active' AND end_time > %s
                ORDER BY start_time ASC LIMIT %s
            """, (now, limit))
        return [dict(r) for r in cur.fetchall()]


def cancel_reservation(reservation_id, user_id):
    """Ownership-checked cancel — only the reservation's own creator can cancel it.
    Returns True if something was actually cancelled, False if not found/not theirs."""
    with _cursor() as cur:
        cur.execute("""
            UPDATE vc_reservations SET status = 'cancelled'
            WHERE id = %s AND user_id = %s AND status = 'active'
            RETURNING id
        """, (reservation_id, str(user_id)))
        return cur.fetchone() is not None


async def reset_and_rebuild_vc_sessions():
    """
    Wipes vc_sessions and rebuilds it from scratch by replaying vc_logs
    again (same logic as the original one-time backfill, just re-triggered
    on demand). vc_logs itself is NEVER touched — this is purely about
    throwing away the *interpreted* table and re-deriving it, which is safe
    to do at any time since vc_logs remains the permanent source of truth.

    Returns (session_count, still_open_count) for the caller to report back.
    """
    with _cursor() as cur:
        cur.execute("TRUNCATE TABLE vc_sessions")
        cur.execute("DELETE FROM bot_settings WHERE key = 'vc_sessions_backfilled'")

    backfill_vc_sessions_from_logs()
    await reconcile_vc_sessions()

    with _cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM vc_sessions")
        total = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM vc_sessions WHERE ended_at IS NULL")
        still_open = cur.fetchone()["c"]
    return total, still_open


class ReservationDetailsModal(discord.ui.Modal, title="Reserve this channel"):
    date_str = discord.ui.TextInput(label="Date (YYYY-MM-DD)", placeholder="2026-01-20", max_length=10)
    time_str = discord.ui.TextInput(label="Time, 24h, your local time (HH:MM)", placeholder="19:30", max_length=5)
    duration_str = discord.ui.TextInput(label="Duration in minutes", placeholder="60", max_length=4)

    def __init__(self, channel_id: int, channel_name: str):
        super().__init__()
        self.channel_id = channel_id
        self.channel_name = channel_name

    async def on_submit(self, interaction: discord.Interaction):
        try:
            naive_local = datetime.strptime(f"{self.date_str}".strip() + " " + f"{self.time_str}".strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message("Couldn't parse that date/time — use YYYY-MM-DD and HH:MM.", ephemeral=True)
            return
        try:
            duration = int(str(self.duration_str).strip())
        except ValueError:
            await interaction.response.send_message("Duration needs to be a whole number of minutes.", ephemeral=True)
            return
        if duration <= 0 or duration > MAX_RESERVATION_MINUTES:
            await interaction.response.send_message(f"Duration must be between 1 and {MAX_RESERVATION_MINUTES} minutes.", ephemeral=True)
            return

        start_utc = naive_local.replace(tzinfo=timezone.utc) - timedelta(hours=TIMEZONE_OFFSET_HOURS)
        if start_utc < datetime.now(timezone.utc):
            await interaction.response.send_message("That time is already in the past.", ephemeral=True)
            return

        ok, result = create_reservation(
            interaction.user.id, interaction.user.display_name,
            self.channel_id, self.channel_name, start_utc, duration,
        )
        if ok:
            end_local = naive_local + timedelta(minutes=duration)
            await interaction.response.send_message(
                f"\u2705 Reserved **#{self.channel_name}** from {naive_local.strftime('%b %d, %H:%M')} "
                f"to {end_local.strftime('%H:%M')}.",
                ephemeral=True,
            )
        else:
            conflict_start_local = result["start_time"] + timedelta(hours=TIMEZONE_OFFSET_HOURS)
            await interaction.response.send_message(
                f"\u274C That overlaps an existing reservation by {result['user_name']} starting "
                f"{conflict_start_local.strftime('%b %d, %H:%M')}. Pick a different time.",
                ephemeral=True,
            )


class ReservationChannelSelect(discord.ui.Select):
    def __init__(self):
        guild = bot.get_guild(GUILD_ID)
        channels = list(guild.voice_channels)[:25] if guild else []
        options = [discord.SelectOption(label=f"\U0001F50A {c.name}", value=str(c.id)) for c in channels]
        super().__init__(
            placeholder="Select a voice channel to reserve...",
            options=options or [discord.SelectOption(label="No voice channels found", value="none")],
            disabled=not options,
        )

    async def callback(self, interaction: discord.Interaction):
        guild = bot.get_guild(GUILD_ID)
        channel = guild.get_channel(int(self.values[0])) if guild else None
        if channel is None:
            await interaction.response.send_message("Couldn't find that channel.", ephemeral=True)
            return
        await interaction.response.send_modal(ReservationDetailsModal(channel.id, channel.name))


class ReservationChannelSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ReservationChannelSelect())


class CancelReservationSelect(discord.ui.Select):
    def __init__(self, reservations: list):
        options = [
            discord.SelectOption(
                label=f"#{r['channel_name']} \u2014 {(r['start_time'] + timedelta(hours=TIMEZONE_OFFSET_HOURS)).strftime('%b %d, %H:%M')}",
                value=str(r["id"]),
            )
            for r in reservations
        ]
        super().__init__(placeholder="Select a reservation to cancel...", options=options)

    async def callback(self, interaction: discord.Interaction):
        reservation_id = int(self.values[0])
        cancelled = cancel_reservation(reservation_id, interaction.user.id)
        if cancelled:
            await interaction.response.edit_message(content="\u2705 Reservation cancelled.", view=None)
        else:
            await interaction.response.edit_message(content="Couldn't cancel that \u2014 it may already be gone.", view=None)


class CancelReservationSelectView(discord.ui.View):
    def __init__(self, reservations: list):
        super().__init__(timeout=120)
        self.add_item(CancelReservationSelect(reservations))


# ---------- Shared handlers — used by BOTH slash commands and the menu panel ----------

async def run_vc_reserve(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Pick a voice channel to reserve:", view=ReservationChannelSelectView(), ephemeral=True
    )


async def run_vc_reservations_list(interaction: discord.Interaction):
    reservations = list_upcoming_reservations(limit=10)
    if not reservations:
        await interaction.response.send_message("No upcoming reservations.", ephemeral=True)
        return
    lines = [
        f"**#{r['channel_name']}** \u2014 {(r['start_time'] + timedelta(hours=TIMEZONE_OFFSET_HOURS)).strftime('%b %d, %H:%M')} "
        f"({int((r['end_time'] - r['start_time']).total_seconds() // 60)}m) by {r['user_name']}"
        for r in reservations
    ]
    embed = discord.Embed(title="\U0001F50A Upcoming VC Reservations", description="\n".join(lines), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def run_cancel_my_reservation(interaction: discord.Interaction):
    reservations = list_upcoming_reservations(user_id=interaction.user.id, limit=25)
    if not reservations:
        await interaction.response.send_message("You have no upcoming reservations.", ephemeral=True)
        return
    await interaction.response.send_message(
        "Pick one to cancel:", view=CancelReservationSelectView(reservations), ephemeral=True
    )


async def run_who_was_with_me(interaction: discord.Interaction):
    sessions = fetch_sessions_for_period(None, None)  # all-time, matching the spec's example
    results = vs.compute_who_was_with_me(str(interaction.user.id), sessions)
    if not results:
        await interaction.response.send_message("No overlapping VC time with anyone yet.", ephemeral=True)
        return
    lines = [f"**{r['user_name']}**\n\u23F1\uFE0F Together: {vs.format_duration(r['seconds'])}" for r in results]
    embed = discord.Embed(title="\U0001F465 WHO WAS WITH ME?", description="\n\n".join(lines), color=discord.Color.purple())
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def run_my_vc_report(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    now = datetime.now(timezone.utc)

    def total_for(period):
        p_start, p_end = vs.period_bounds(period, now=now, tz_offset_hours=TIMEZONE_OFFSET_HOURS)
        raw = fetch_sessions_for_period(p_start, p_end)
        clipped = vs.clip_sessions_to_period(raw, p_start, p_end, now=now)
        return sum(s["duration_seconds"] for s in clipped if s["user_id"] == uid)

    today_secs = total_for("today")
    week_secs = total_for("week")
    month_secs = total_for("month")

    all_sessions = [s for s in fetch_sessions_for_period(None, None) if s["user_id"] == uid]
    all_clipped = vs.clip_sessions_to_period(all_sessions, None, None, now=now)
    channel_totals_mine = vs.channel_totals(all_clipped, top=1)
    most_used = channel_totals_mine[0]["channel_name"] if channel_totals_mine else "\u2014"

    embed = discord.Embed(title="\U0001F4CA YOUR VC REPORT", color=discord.Color.gold())
    embed.add_field(name="\u23F1\uFE0F Today", value=vs.format_duration(today_secs), inline=True)
    embed.add_field(name="\U0001F4C5 This Week", value=vs.format_duration(week_secs), inline=True)
    embed.add_field(name="\U0001F5D3\uFE0F This Month", value=vs.format_duration(month_secs), inline=True)
    embed.add_field(name="\U0001F3C6 Most Used VC", value=most_used, inline=True)
    embed.add_field(name="\U0001F399\uFE0F Sessions", value=str(len(all_clipped)), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def run_admin_list(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("\U0001F512 You don't have permission to view this.", ephemeral=True)
        return
    admins = [m for m in interaction.guild.members if not m.bot and m.guild_permissions.administrator]
    lines = [f"\u2705 {m.display_name}" for m in admins] or ["No administrators found."]
    embed = discord.Embed(title="\U0001F451 ADMIN LIST", description="\n".join(lines), color=discord.Color.red())
    embed.set_footer(text="\U0001F512 Only administrators can see this message.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class VCMenuView(discord.ui.View):
    """Persistent public panel bundling all four new features into one place,
    matching the existing Select/Button pattern used elsewhere (e.g.
    JoinButtonView) rather than a separate UI system. Each button just calls
    the same shared handler the equivalent slash command uses — no logic is
    duplicated between the two entry points."""

    def __init__(self):
        super().__init__(timeout=None)
        self.reservations.custom_id = "vc_menu_reservations"
        self.who_was_with_me.custom_id = "vc_menu_who_was_with_me"
        self.my_report.custom_id = "vc_menu_my_report"
        self.admin_list.custom_id = "vc_menu_admin_list"

    @discord.ui.button(label="VC Reservations", emoji="\U0001F50A", style=discord.ButtonStyle.primary, row=0)
    async def reservations(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_vc_reserve(interaction)

    @discord.ui.button(label="Who Was With Me?", emoji="\U0001F465", style=discord.ButtonStyle.secondary, row=0)
    async def who_was_with_me(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_who_was_with_me(interaction)

    @discord.ui.button(label="My VC Report", emoji="\U0001F4CA", style=discord.ButtonStyle.secondary, row=0)
    async def my_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_my_vc_report(interaction)

    @discord.ui.button(label="Admin List", emoji="\U0001F451", style=discord.ButtonStyle.danger, row=1)
    async def admin_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_admin_list(interaction)


class WipeAllVcHistoryConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes, delete EVERYTHING and start from 0", style=discord.ButtonStyle.danger, emoji="\U0001F5D1\uFE0F")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Wiping everything\u2026", view=self)
        try:
            with _cursor() as cur:
                cur.execute("TRUNCATE TABLE vc_logs")
                cur.execute("TRUNCATE TABLE vc_sessions")
                cur.execute("DELETE FROM bot_settings WHERE key = 'vc_sessions_backfilled'")
            # Give anyone currently in voice a fresh session immediately, so
            # tracking starts working again right away instead of waiting
            # for their next join/move/leave.
            await reconcile_vc_sessions()
            await interaction.followup.send(
                "\u2705 All VC history wiped \u2014 vc_logs and vc_sessions are both empty. "
                "Tracking starts fresh from now; anyone currently in a voice channel already has a new open session.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"\u274C Wipe failed: {e}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was deleted.", view=self)


class DeleteVcLogConfirmView(discord.ui.View):
    def __init__(self, log_id: int):
        super().__init__(timeout=60)
        self.log_id = log_id

    @discord.ui.button(label="Yes, delete this row", style=discord.ButtonStyle.danger, emoji="\U0001F5D1\uFE0F")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        with _cursor() as cur:
            cur.execute("DELETE FROM vc_logs WHERE id = %s", (self.log_id,))
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"\u2705 Deleted `#{self.log_id}`. Run `/reset-vc-stats` to rebuild stats without it.",
            view=self,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was deleted.", view=self)


class ResetVcStatsConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes, wipe and rebuild", style=discord.ButtonStyle.danger, emoji="\u26A0\uFE0F")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Rebuilding from vc_logs\u2026 this may take a moment.", view=self)
        try:
            total, still_open = await reset_and_rebuild_vc_sessions()
            await interaction.followup.send(
                f"\u2705 Done. Rebuilt **{total}** session(s) from vc_logs "
                f"({still_open} currently open, matching real voice presence).",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"\u274C Reset failed: {e}", ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was changed.", view=self)


# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True  # required to read DM text for the announcement feature

bot = None      # created by make_bot(), recreated on each reconnect attempt
bot_loop = None

# ---------- In-memory state ----------
# last_split: quick-access cache used by the old random-split feature (kept for backwards compat)
last_split = {"team_a": [], "team_b": []}

# voice_log: fallback activity log used only when no database is configured
voice_log = deque(maxlen=300)

# channel_locks: set of voice channel IDs (int) that are currently locked
# locked_members: dict of user_id (int) -> the one channel ID (int) they're allowed back into
# Both are restored from PostgreSQL on startup — see start_bot().
channel_locks = set()
locked_members = {}

# dashboard_pending: user_id (int) -> True, set right before we ourselves move/disconnect
# someone from an API call. Lets on_voice_state_update tell "a dashboard action just
# happened" apart from "a real Discord moderator/user action just happened", so we can
# (a) attribute the log correctly and (b) not treat our own corrective moves as lock violations.
dashboard_pending = {}


def member_info(m):
    voice = m.voice
    return {
        "id": str(m.id),
        "name": m.display_name,
        "avatar": m.display_avatar.url,
        "muted": bool(voice and voice.mute),
        "deafened": bool(voice and voice.deaf),
    }


class BotNotReady(Exception):
    """Raised when an API call needs the Discord connection but it isn't up yet
    (still connecting, or stuck retrying after a rate limit/outage)."""
    pass


def run_coro(coro):
    """Run an async discord.py coroutine from Flask's sync thread.
    Fails fast with a clear message if the bot isn't currently connected,
    instead of hanging for the full timeout with a blank error."""
    if bot is None or bot_loop is None or not bot.is_ready():
        coro.close()  # avoid an "was never awaited" warning for the coroutine we're not running
        raise BotNotReady(
            "The Discord bot isn't connected right now (it may still be reconnecting "
            "after a rate limit or restart). This should resolve on its own — try again shortly."
        )
    future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return future.result(timeout=15)


def log_event_full_with_session(action, member, from_channel=None, to_channel=None,
                                 actor_id=None, actor_name=None, details=None,
                                 session_action=None):
    """
    Like log_event_full, but for the actions that also affect vc_sessions
    (JOINED, MOVED, LEFT, DISCONNECTED): writes the vc_logs row AND the
    vc_sessions open/close in the SAME database transaction, so a failure
    partway through can never leave vc_logs and vc_sessions inconsistent
    with each other. This replaces the old pattern of two separate
    _cursor() calls (log_event_full, then open_or_move_session/close_session)
    for exactly the event types where that gap mattered.

    session_action:
      "open"  — JOINED/MOVED: close any existing open session for this
                user (defensive), then open a new one at to_channel.
      "close" — LEFT/DISCONNECTED: close the existing open session.
      None    — no vc_sessions effect at all (e.g. LOCK_VIOLATION) —
                behaves exactly like the old log_event_full.
    """
    voice_log.appendleft({
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": str(member.id),
        "name": member.display_name,
        "avatar": member.display_avatar.url,
        "actor_name": actor_name,
        "from": from_channel.name if from_channel else None,
        "to": to_channel.name if to_channel else None,
        "details": details,
    })

    if not _pool:
        return

    user_id = str(member.id)
    user_name = member.display_name
    from_channel_id = str(from_channel.id) if from_channel else None
    from_channel_name = from_channel.name if from_channel else None
    to_channel_id = str(to_channel.id) if to_channel else None
    to_channel_name = to_channel.name if to_channel else None
    at = datetime.now(timezone.utc)

    log_sql = """
        INSERT INTO vc_logs
            (action, user_id, user_name, actor_id, actor_name,
             from_channel_id, from_channel_name, to_channel_id, to_channel_name, details)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    log_params = (action, user_id, user_name, actor_id, actor_name,
                  from_channel_id, from_channel_name, to_channel_id, to_channel_name, details)

    for attempt in range(2):  # one retry — the first attempt may hit a stale pooled connection
        try:
            with _cursor() as cur:
                cur.execute(log_sql, log_params)

                if session_action == "open":
                    cur.execute("""
                        UPDATE vc_sessions
                        SET ended_at = %s, duration_seconds = EXTRACT(EPOCH FROM (%s - started_at))
                        WHERE user_id = %s AND ended_at IS NULL
                    """, (at, at, user_id))
                    cur.execute("""
                        INSERT INTO vc_sessions
                            (user_id, user_name, channel_id, channel_name, started_at)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (user_id, user_name, to_channel_id, to_channel_name, at))
                elif session_action == "close":
                    cur.execute("""
                        UPDATE vc_sessions
                        SET ended_at = %s, duration_seconds = EXTRACT(EPOCH FROM (%s - started_at))
                        WHERE user_id = %s AND ended_at IS NULL
                    """, (at, at, user_id))
            return
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            if attempt == 0:
                continue  # _cursor() already discarded the dead connection — retry with a fresh one
            print(f"[db] log_event_full_with_session failed after retry: {e}", flush=True)
        except psycopg2.errors.UniqueViolation:
            print(
                f"[vc_sessions] Unexpected duplicate-open race for user {user_id} during atomic write — "
                f"will self-heal on next reconciliation.", flush=True,
            )
            return
        except Exception as e:
            print(f"[db] log_event_full_with_session failed: {e}", flush=True)
            return


def log_event_full(action, member, from_channel=None, to_channel=None,
                    actor_id=None, actor_name=None, details=None):
    """Write one activity log entry to both the in-memory fallback and PostgreSQL (if configured).
    Used only for events that DON'T affect vc_sessions (e.g. LOCK_VIOLATION) —
    see log_event_full_with_session for JOINED/MOVED/LEFT/DISCONNECTED, which
    need the vc_logs + vc_sessions writes to be atomic."""
    voice_log.appendleft({
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "user_id": str(member.id),
        "name": member.display_name,
        "avatar": member.display_avatar.url,
        "actor_name": actor_name,
        "from": from_channel.name if from_channel else None,
        "to": to_channel.name if to_channel else None,
        "details": details,
    })
    log_event(
        action=action,
        user_id=str(member.id), user_name=member.display_name,
        actor_id=actor_id, actor_name=actor_name,
        from_channel_id=str(from_channel.id) if from_channel else None,
        from_channel_name=from_channel.name if from_channel else None,
        to_channel_id=str(to_channel.id) if to_channel else None,
        to_channel_name=to_channel.name if to_channel else None,
        details=details,
    )


async def find_recent_voice_actor(guild, action_type, from_channel_id=None, to_channel_id=None, within_seconds=6):
    """
    Best-effort lookup of who triggered a MOVE or DISCONNECT, via Discord's audit log.

    IMPORTANT LIMITATION: Discord's audit log entries for MEMBER_MOVE and
    MEMBER_DISCONNECT do not identify which specific member was affected —
    they only record an affected channel and a `count` of how many members
    were moved/disconnected together in that single action. There is no
    per-member target on these entry types.

    So this can only ever be a heuristic: we look at the most recent matching
    entry, require it happened within `within_seconds`, and only attribute it
    if `count == 1` (a single-member action — safe to assume it's ours).
    If count > 1 (a bulk "move all" / "disconnect all"), or nothing recent
    enough is found, we deliberately return None instead of guessing.
    Also returns None if the bot lacks the "View Audit Log" permission.
    """
    action_map = {
        "MOVE": discord.AuditLogAction.member_move,
        "DISCONNECT": discord.AuditLogAction.member_disconnect,
    }
    audit_action = action_map.get(action_type)
    if audit_action is None:
        return None
    try:
        async for entry in guild.audit_logs(action=audit_action, limit=10):
            age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
            if age > within_seconds:
                break  # entries come back newest-first; older than this isn't ours
            count = getattr(entry.extra, "count", None)
            entry_channel = getattr(entry.extra, "channel", None)
            entry_channel_id = entry_channel.id if entry_channel else None
            if count != 1:
                continue
            if action_type == "MOVE" and entry_channel_id not in (from_channel_id, to_channel_id):
                continue
            return entry.user
        return None
    except discord.Forbidden:
        return None
    except Exception as e:
        print(f"[audit] lookup failed: {e}")
        return None


async def list_voice_channels():
    guild = bot.get_guild(GUILD_ID)
    channels = [c for c in guild.channels if isinstance(c, discord.VoiceChannel)]
    if ALLOWED_CATEGORY_ID:
        cat_id = int(ALLOWED_CATEGORY_ID)
        channels = [c for c in channels if c.category_id == cat_id]
    channels.sort(key=lambda c: c.position)
    return [
        {
            "id": str(c.id), "name": c.name, "member_count": len(c.members),
            "locked": c.id in channel_locks,
        }
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
        dashboard_pending[m.id] = True
        try:
            await m.move_to(channel, reason="Moved via VC Control dashboard")
            moved.append(member_info(m))
        except discord.Forbidden:
            dashboard_pending.pop(m.id, None)
            failed.append({"name": m.display_name, "reason": "Bot lacks Move Members permission"})
        except discord.HTTPException as e:
            dashboard_pending.pop(m.id, None)
            failed.append({"name": m.display_name, "reason": str(e)})
    return moved, failed


async def disconnect_members(members):
    moved = []
    failed = []
    for m in members:
        dashboard_pending[m.id] = True
        try:
            await m.move_to(None, reason="Disconnected via VC Control dashboard")
            moved.append(member_info(m))
        except discord.Forbidden:
            dashboard_pending.pop(m.id, None)
            failed.append({"name": m.display_name, "reason": "Bot lacks Move Members permission"})
        except discord.HTTPException as e:
            dashboard_pending.pop(m.id, None)
            failed.append({"name": m.display_name, "reason": str(e)})
    return moved, failed


async def reconcile_stale_sessions():
    """
    Called once the bot reconnects. If the bot was offline for a while (rate
    limit, restart, etc.), it missed whatever LEFT/DISCONNECTED events happened
    during that gap — so the logs still show those people as "in" a channel.
    Left alone, every future leaderboard calculation would treat them as still
    there and keep extending their time forever. This compares who the logs
    *think* is still in voice against who's actually there right now, and
    closes out anyone who isn't — so their time stops at roughly when the bot
    went down, not keeps growing indefinitely.

    This is the OLD, vc_logs-side reconciliation and is unchanged from before
    the vc_sessions work — it keeps the raw event log itself coherent.
    reconcile_vc_sessions() (called right after this, in on_ready) does the
    equivalent job for the new vc_sessions table, and additionally opens
    sessions for people already present with no session on record — the gap
    this function was never designed to cover.
    """
    if not is_enabled():
        return
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    events = fetch_events_since(days_back=90)
    open_sessions = {}
    for e in events:
        action = (e.get("action") or "").upper()
        if action == "LOCK_VIOLATION":
            continue
        uid = e["user_id"]
        if action in ("JOINED", "MOVED"):
            open_sessions[uid] = {"channel_name": e.get("to_channel_name"), "user_name": e.get("user_name")}
        elif action in ("LEFT", "DISCONNECTED"):
            open_sessions.pop(uid, None)

    currently_in_voice = set()
    for vc in guild.channels:
        if isinstance(vc, discord.VoiceChannel):
            for m in vc.members:
                currently_in_voice.add(str(m.id))

    closed_count = 0
    for uid, info in open_sessions.items():
        if uid not in currently_in_voice:
            log_event(
                action="LEFT",
                user_id=uid, user_name=info["user_name"],
                from_channel_name=info["channel_name"],
                details="Auto-closed on reconnect — bot was offline when this person actually left",
            )
            closed_count += 1

    if closed_count:
        print(f"[startup] Reconciled {closed_count} stale open session(s) after reconnect.", flush=True)


# Comma-separated Discord user IDs allowed to DM the bot an announcement, e.g. "111,222"
ANNOUNCE_ADMIN_IDS = {
    uid.strip() for uid in os.environ.get("ANNOUNCE_ADMIN_IDS", "").split(",") if uid.strip()
}
if not ANNOUNCE_ADMIN_IDS:
    print("[announce] ANNOUNCE_ADMIN_IDS env var not set — no one will be able to DM announcements.", flush=True)


class AnnouncementChannelSelect(discord.ui.Select):
    """Lets the announcer tick one or more channels in their DM before it posts."""

    def __init__(self, content: str, attachments_data: list):
        guild = bot.get_guild(GUILD_ID)
        text_channels = list(guild.text_channels)[:25] if guild else []  # Discord caps select options at 25
        options = [discord.SelectOption(label=f"#{c.name}", value=str(c.id)) for c in text_channels]
        super().__init__(
            placeholder="Select channel(s) to post to...",
            min_values=1,
            max_values=max(len(options), 1),
            options=options or [discord.SelectOption(label="No channels found", value="none")],
            disabled=not options,
        )
        self.content = content
        self.attachments_data = attachments_data  # list of (filename, bytes)

    async def callback(self, interaction: discord.Interaction):
        posted, failed = [], []
        text = f"@everyone\n\n{self.content}" if self.content else "@everyone"
        for channel_id_str in self.values:
            channel = bot.get_channel(int(channel_id_str))
            if channel is None:
                failed.append(channel_id_str)
                continue
            try:
                files = [discord.File(io.BytesIO(data), filename=fname) for fname, data in self.attachments_data]
                await channel.send(
                    content=text,
                    files=files if files else None,
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
                posted.append(channel.name)
            except discord.Forbidden:
                failed.append(f"#{channel.name} (no permission)")
            except Exception as e:
                failed.append(f"#{channel.name} ({e})")

        summary = f"✅ Posted to: {', '.join('#' + n for n in posted)}" if posted else "Nothing was posted."
        if failed:
            summary += f"\n⚠️ Couldn't post to: {', '.join(failed)}"
        for child in self.view.children:
            child.disabled = True
        await interaction.response.edit_message(content=summary, view=self.view)


class AnnouncementChannelView(discord.ui.View):
    def __init__(self, content: str, attachments_data: list):
        super().__init__(timeout=600)  # 10 min to pick channels before the prompt goes stale
        self.add_item(AnnouncementChannelSelect(content, attachments_data))


async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is not None:
        return  # only act on DMs — ignore server messages entirely

    if str(message.author.id) not in ANNOUNCE_ADMIN_IDS:
        return  # not an authorized announcer — stay silent, don't confirm/deny bot behavior to strangers

    if not get_toggle("announcements"):
        await message.reply("The announcement system is turned off right now (`/toggle`).")
        return

    if not message.content and not message.attachments:
        await message.reply("Send some text and/or an image to announce — that message was empty.")
        return

    attachments_data = [(a.filename, await a.read()) for a in message.attachments]
    await message.reply(
        "Where should this go? Tick one channel or several.",
        view=AnnouncementChannelView(message.content, attachments_data),
    )


async def on_ready():
    print(f"Bot ready as {bot.user}", flush=True)
    try:
        await reconcile_stale_sessions()
    except Exception as e:
        print(f"[startup] Session reconciliation failed (non-fatal): {e}", flush=True)
    try:
        await reconcile_vc_sessions()
    except Exception as e:
        print(f"[vc_sessions] Reconciliation failed (non-fatal): {e}", flush=True)
    try:
        await restore_join_views()
        await restore_link_expired_views()
        bot.add_view(JoinButtonView())
        bot.add_view(VCMenuView())
        await ensure_join_button_posted()
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    except Exception as e:
        print(f"[join] Failed to sync /join-whatsapp command (non-fatal): {e}", flush=True)
    try:
        await restore_birthday_views()
        await backfill_birthday_nudges()
        if not hasattr(bot, "_birthday_task_started"):
            bot._birthday_task_started = True
            asyncio.create_task(daily_birthday_check_loop())
    except Exception as e:
        print(f"[birthday] Startup tasks failed (non-fatal): {e}", flush=True)


async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    if not get_toggle("birthday_new"):
        return
    await nudge_member_for_birthday(member, force=True)


async def on_voice_state_update(member, before, after):
    if member.guild.id != GUILD_ID:
        return

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None
    if before_id == after_id:
        return  # mute/deafen-only change, not an actual channel change

    is_dashboard_action = dashboard_pending.pop(member.id, False)

    # ---------- VC lock enforcement ----------
    if after_id is not None and not is_dashboard_action:
        violation_reason = None
        if member.id in locked_members:
            allowed_id = locked_members[member.id]
            if after_id != allowed_id:
                allowed_channel = member.guild.get_channel(allowed_id)
                violation_reason = f"only allowed back into {allowed_channel.name if allowed_channel else 'their assigned channel'}"
        elif after_id in channel_locks:
            violation_reason = "this channel is locked to a specific group"

        if violation_reason:
            try:
                if LOCK_VIOLATION_ACTION == "move_back" and member.id in locked_members:
                    allowed_channel = member.guild.get_channel(locked_members[member.id])
                    if allowed_channel:
                        dashboard_pending[member.id] = True  # this corrective move isn't itself a violation
                        await member.move_to(allowed_channel, reason=f"VC lock violation: {violation_reason}")
                    else:
                        await member.move_to(None, reason=f"VC lock violation: {violation_reason}")
                else:
                    await member.move_to(None, reason=f"VC lock violation: {violation_reason}")
            except discord.Forbidden:
                pass
            log_event_full(
                "LOCK_VIOLATION", member=member, to_channel=after.channel,
                details=f"{violation_reason} (action: {LOCK_VIOLATION_ACTION})",
            )
            return

    # ---------- Normal join / leave / move logging ----------
    if before_id is None and after_id is not None:
        log_event_full_with_session("JOINED", member=member, to_channel=after.channel, session_action="open")
        return

    if before_id is not None and after_id is None:
        actor_id = actor_name = None
        if is_dashboard_action:
            actor_name = "VC Control (Website)"
        else:
            actor = await find_recent_voice_actor(member.guild, "DISCONNECT", before_id)
            if actor:
                actor_id, actor_name = str(actor.id), str(actor)
        log_event_full_with_session(
            "DISCONNECTED" if actor_name else "LEFT",
            member=member, from_channel=before.channel,
            actor_id=actor_id, actor_name=actor_name,
            session_action="close",
        )
        return

    if before_id is not None and after_id is not None:
        actor_id = actor_name = None
        if is_dashboard_action:
            actor_name = "VC Control (Website)"
        else:
            actor = await find_recent_voice_actor(member.guild, "MOVE", before_id, after_id)
            if actor:
                actor_id, actor_name = str(actor.id), str(actor)
        log_event_full_with_session(
            "MOVED", member=member, from_channel=before.channel, to_channel=after.channel,
            actor_id=actor_id, actor_name=actor_name,
            session_action="open",
        )


def make_bot():
    """Build a fresh discord.Client with our handlers attached. Called on startup
    and again on every reconnect attempt, since a Client that failed to log in
    isn't safe to reuse — safer to start clean each time."""
    client = discord.Client(intents=intents)
    client.event(on_ready)
    client.event(on_voice_state_update)
    client.event(on_message)
    client.event(on_member_join)
    client.tree = app_commands.CommandTree(client)

    @client.tree.command(name="join-whatsapp", description="Request to join the WhatsApp squad")
    async def join_whatsapp(interaction: discord.Interaction):
        await interaction.response.send_modal(JoinModal())

    @client.tree.command(name="register-birthday", description="Register or update your birthday")
    async def register_birthday_cmd(interaction: discord.Interaction):
        await interaction.response.send_modal(BirthdayModal())

    @client.tree.command(name="my-birthday", description="Check what birthday you have on file")
    async def my_birthday_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute("SELECT day, month, year FROM birthdays WHERE user_id = %s", (str(interaction.user.id),))
            row = cur.fetchone()
        if row is None:
            await interaction.response.send_message("You haven't registered a birthday yet — use `/register-birthday`.", ephemeral=True)
            return
        text = f"{row['month']}/{row['day']}" + (f"/{row['year']}" if row["year"] else "")
        await interaction.response.send_message(f"On file: {text}", ephemeral=True)

    @client.tree.command(name="remove-birthday", description="Remove your birthday from the system")
    async def remove_birthday_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute("DELETE FROM birthdays WHERE user_id = %s", (str(interaction.user.id),))
        await interaction.response.send_message("Removed.", ephemeral=True)

    @client.tree.command(name="set-birthday-channel", description="Set where birthday wishes get posted (admin only)")
    @app_commands.describe(channel="The channel to post birthday wishes in")
    @app_commands.default_permissions(administrator=True)
    async def set_birthday_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel):
        set_birthday_channel_id(channel.id)
        await interaction.response.send_message(f"Birthday wishes will now post in {channel.mention}.", ephemeral=True)

    @client.tree.command(name="set-birthday-reminder-interval", description="How often to re-nudge members who haven't set a birthday (admin only)")
    @app_commands.describe(value="How many units between reminders", unit="Which time unit")
    @app_commands.choices(unit=[
        app_commands.Choice(name="minutes", value="minutes"),
        app_commands.Choice(name="hours", value="hours"),
        app_commands.Choice(name="days", value="days"),
        app_commands.Choice(name="years", value="years"),
    ])
    @app_commands.default_permissions(administrator=True)
    async def set_birthday_reminder_interval_cmd(interaction: discord.Interaction, value: float, unit: app_commands.Choice[str]):
        if value <= 0:
            await interaction.response.send_message("Value has to be more than 0.", ephemeral=True)
            return
        multiplier = {"minutes": 1 / 60, "hours": 1, "days": 24, "years": 24 * 365}[unit.value]
        hours = value * multiplier
        set_birthday_reminder_interval_hours(hours)
        recurring = get_toggle("birthday_reminder_recurring")
        note = "" if recurring else "\n\u26A0\uFE0F Note: recurring reminders are currently **off** (once-only mode) — this interval won't take effect until you turn that back on with `/toggle`."
        await interaction.response.send_message(
            f"\u2705 Reminder interval set to {value:g} {unit.value} (~{hours:.1f}h).{note}", ephemeral=True
        )

    @client.tree.command(name="daily", description="Claim your daily points")
    async def daily_cmd(interaction: discord.Interaction):
        if not get_toggle("gambling"):
            await interaction.response.send_message("The points system is turned off right now.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"{interaction.user.mention} tap to claim your daily {DAILY_CLAIM_AMOUNT} points:",
            view=DailyClaimView(interaction.user.id),
        )

    @client.tree.command(name="balance", description="Check your points balance")
    async def balance_cmd(interaction: discord.Interaction):
        if not get_toggle("gambling"):
            await interaction.response.send_message("The points system is turned off right now.", ephemeral=True)
            return
        await interaction.response.send_message(f"{interaction.user.mention}: {get_balance(interaction.user.id)} points")

    @client.tree.command(name="coinflip", description="Challenge someone to a points coinflip duel")
    @app_commands.describe(amount="How many points to bet")
    async def coinflip_cmd(interaction: discord.Interaction, amount: int):
        if not get_toggle("gambling"):
            await interaction.response.send_message("The points system is turned off right now.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Bet has to be more than 0.", ephemeral=True)
            return
        if get_balance(interaction.user.id) < amount:
            await interaction.response.send_message("You don't have enough points for that bet.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"\U0001FA99 {interaction.user.mention} is challenging anyone to a **{amount}**-point coinflip! First to accept plays.",
            view=CoinflipAcceptView(interaction.user.id, amount),
        )

    @client.tree.command(name="slots", description="Spin the slots for points")
    @app_commands.describe(amount="How many points to bet")
    async def slots_cmd(interaction: discord.Interaction, amount: int):
        if not get_toggle("gambling"):
            await interaction.response.send_message("The points system is turned off right now.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Bet has to be more than 0.", ephemeral=True)
            return
        if get_balance(interaction.user.id) < amount:
            await interaction.response.send_message("You don't have enough points for that bet.", ephemeral=True)
            return
        content, _ = run_slots_spin(interaction.user.id, amount)
        await interaction.response.send_message(content, view=SlotsSpinView(interaction.user.id, amount))

    @client.tree.command(name="dice", description="Guess higher or lower than the roll")
    @app_commands.describe(amount="How many points to bet")
    async def dice_cmd(interaction: discord.Interaction, amount: int):
        if not get_toggle("gambling"):
            await interaction.response.send_message("The points system is turned off right now.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Bet has to be more than 0.", ephemeral=True)
            return
        if get_balance(interaction.user.id) < amount:
            await interaction.response.send_message("You don't have enough points for that bet.", ephemeral=True)
            return
        current = random.randint(1, 6)
        await interaction.response.send_message(
            f"{interaction.user.mention} rolled a **{current}**. Higher or lower on the next roll?",
            view=DiceHighLowView(interaction.user.id, amount, current),
        )

    @client.tree.command(name="points-leaderboard", description="See who has the most points")
    async def points_leaderboard_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute("SELECT user_id, balance FROM points ORDER BY balance DESC LIMIT 10")
            rows = cur.fetchall()
        if not rows:
            await interaction.response.send_message("No one has any points yet.", ephemeral=True)
            return
        lines = [f"{i+1}. <@{row['user_id']}> \u2014 {row['balance']} points" for i, row in enumerate(rows)]
        embed = discord.Embed(title="\U0001F3C6 Points Leaderboard", description="\n".join(lines), color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @client.tree.command(name="whatsapp-list", description="See everyone who filled the WhatsApp join form (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def whatsapp_list_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute(
                "SELECT user_id, name, status, created_at FROM join_requests ORDER BY created_at DESC LIMIT 25"
            )
            rows = cur.fetchall()
        if not rows:
            await interaction.response.send_message("No WhatsApp join requests yet.", ephemeral=True)
            return
        status_emoji = {"pending": "\u23F3", "approved": "\u2705", "denied": "\u274C", "on_hold": "\u23F8\uFE0F"}
        lines = [
            f"{status_emoji.get(r['status'], '')} **{r['name']}** (<@{r['user_id']}>) \u2014 {r['status']}"
            for r in rows
        ]
        embed = discord.Embed(title="WhatsApp join requests (most recent 25)", description="\n".join(lines), color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @client.tree.command(name="birthday-list", description="See everyone who registered a birthday (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def birthday_list_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute("SELECT user_id, day, month, year FROM birthdays ORDER BY month, day LIMIT 50")
            rows = cur.fetchall()
        if not rows:
            await interaction.response.send_message("No birthdays registered yet.", ephemeral=True)
            return
        lines = [
            f"**{r['month']}/{r['day']}**" + (f"/{r['year']}" if r["year"] else "") + f" \u2014 <@{r['user_id']}>"
            for r in rows
        ]
        embed = discord.Embed(title="Registered birthdays", description="\n".join(lines), color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @client.tree.command(name="set-whatsapp-link", description="Update the WhatsApp invite link (admin only)")
    @app_commands.describe(new_link="The new https://chat.whatsapp.com/... invite link")
    @app_commands.default_permissions(administrator=True)
    async def set_whatsapp_link_cmd(interaction: discord.Interaction, new_link: str):
        if not new_link.startswith("https://chat.whatsapp.com/"):
            await interaction.response.send_message(
                "That doesn't look like a WhatsApp invite link — expected it to start with "
                "https://chat.whatsapp.com/", ephemeral=True
            )
            return

        set_whatsapp_link(new_link)
        await interaction.response.send_message(
            "Link updated. Re-DMing everyone already approved now...", ephemeral=True
        )

        with _cursor() as cur:
            cur.execute("SELECT DISTINCT user_id FROM join_requests WHERE status = 'approved'")
            approved_user_ids = [row["user_id"] for row in cur.fetchall()]

        sent, failed = 0, 0
        for uid in approved_user_ids:
            try:
                user = client.get_user(int(uid)) or await client.fetch_user(int(uid))
                await user.send(f"The WhatsApp squad link was updated. New link: {new_link}")
                sent += 1
            except (discord.Forbidden, discord.NotFound):
                failed += 1

        await interaction.followup.send(
            f"Done — notified {sent} approved member(s)." + (f" ({failed} couldn't be DMed.)" if failed else ""),
            ephemeral=True,
        )

    @client.tree.command(name="toggle", description="Turn a bot feature on/off (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def toggle_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            "Pick a feature to toggle:", view=ToggleFeatureSelectView(), ephemeral=True
        )

    @client.tree.command(name="inspect-vc-logs", description="View a member's raw VC log history to find bad data (admin only)")
    @app_commands.describe(member="Whose raw log history to inspect", limit="How many rows to show (default 30, max 50)")
    @app_commands.default_permissions(administrator=True)
    async def inspect_vc_logs_cmd(interaction: discord.Interaction, member: discord.Member, limit: int = 30):
        limit = max(1, min(limit, 50))
        with _cursor() as cur:
            cur.execute(
                "SELECT id, action, from_channel_name, to_channel_name, created_at "
                "FROM vc_logs WHERE user_id = %s ORDER BY created_at ASC LIMIT %s",
                (str(member.id), limit),
            )
            rows = cur.fetchall()
        if not rows:
            await interaction.response.send_message(f"No vc_logs history for {member.mention}.", ephemeral=True)
            return

        lines = []
        prev_time = None
        for r in rows:
            gap_note = ""
            if prev_time:
                gap_hours = (r["created_at"] - prev_time).total_seconds() / 3600
                if gap_hours > 12:
                    gap_note = f"  \u26A0\uFE0F **{gap_hours:.1f}h gap before this**"
            ts = r["created_at"].strftime("%Y-%m-%d %H:%M UTC")
            ch = r["to_channel_name"] or r["from_channel_name"] or "\u2014"
            lines.append(f"`#{r['id']}` {ts} \u2014 **{r['action']}** ({ch}){gap_note}")
            prev_time = r["created_at"]

        embed = discord.Embed(
            title=f"Raw vc_logs for {member.display_name} (oldest {len(rows)} shown)",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        embed.set_footer(text="Large gaps flagged above are the usual cause of an inflated 'longest session'. Use /delete-vc-log <id> to remove a bad row.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @client.tree.command(name="delete-vc-log", description="Permanently delete one raw vc_logs row by ID (admin only)")
    @app_commands.describe(log_id="The #id shown by /inspect-vc-logs")
    @app_commands.default_permissions(administrator=True)
    async def delete_vc_log_cmd(interaction: discord.Interaction, log_id: int):
        with _cursor() as cur:
            cur.execute("SELECT id, action, user_name, created_at FROM vc_logs WHERE id = %s", (log_id,))
            row = cur.fetchone()
        if not row:
            await interaction.response.send_message(f"No vc_logs row with id #{log_id}.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Delete `#{row['id']}` \u2014 **{row['action']}** for {row['user_name']} at "
            f"{row['created_at'].strftime('%Y-%m-%d %H:%M UTC')}?\n"
            f"This is permanent. Run `/reset-vc-stats` afterward to rebuild stats without it.",
            view=DeleteVcLogConfirmView(log_id),
            ephemeral=True,
        )

    @client.tree.command(name="wipe-all-vc-history", description="DELETE ALL VC tracking history, including raw logs, and start from zero (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def wipe_all_vc_history_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            "\u26A0\uFE0F **This is different from /reset-vc-stats.** This permanently deletes the raw "
            "`vc_logs` audit trail too, not just the computed stats. There is no undo and nothing to "
            "rebuild from afterward \u2014 leaderboards, longest session, everything starts completely "
            "empty from this moment. Are you sure?",
            view=WipeAllVcHistoryConfirmView(),
            ephemeral=True,
        )

    @client.tree.command(name="vc-reserve", description="Reserve a voice channel for a future date/time")
    async def vc_reserve_cmd(interaction: discord.Interaction):
        await run_vc_reserve(interaction)

    @client.tree.command(name="vc-reservations", description="See upcoming VC reservations")
    async def vc_reservations_cmd(interaction: discord.Interaction):
        await run_vc_reservations_list(interaction)

    @client.tree.command(name="cancel-reservation", description="Cancel one of your own VC reservations")
    async def cancel_reservation_cmd(interaction: discord.Interaction):
        await run_cancel_my_reservation(interaction)

    @client.tree.command(name="who-was-with-me", description="See how much overlapping VC time you've shared with others")
    async def who_was_with_me_cmd(interaction: discord.Interaction):
        await run_who_was_with_me(interaction)

    @client.tree.command(name="my-vc-report", description="See your own personal VC stats")
    async def my_vc_report_cmd(interaction: discord.Interaction):
        await run_my_vc_report(interaction)

    @client.tree.command(name="admin-list", description="See the server's administrators (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def admin_list_cmd(interaction: discord.Interaction):
        await run_admin_list(interaction)

    @client.tree.command(name="vc-menu", description="Post the VC features panel (Reservations / Who Was With Me / My Report / Admin List)")
    @app_commands.default_permissions(administrator=True)
    async def vc_menu_cmd(interaction: discord.Interaction):
        embed = discord.Embed(
            title="\U0001F3AE VC Control Panel",
            description=(
                "\U0001F50A **VC Reservations** \u2014 book a channel for later\n"
                "\U0001F465 **Who Was With Me?** \u2014 see your overlap with others\n"
                "\U0001F4CA **My VC Report** \u2014 your personal stats\n"
                "\U0001F451 **Admin List** \u2014 admins only"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=VCMenuView())

    @client.tree.command(name="reset-vc-stats", description="Wipe and rebuild VC session stats from raw logs (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def reset_vc_stats_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            "\u26A0\uFE0F This will **delete all current VC session stats** and rebuild them from raw "
            "vc_logs history. The raw logs themselves are never touched, so this is recoverable, but "
            "any manual corrections made only to vc_sessions would be lost. Continue?",
            view=ResetVcStatsConfirmView(),
            ephemeral=True,
        )

    @client.tree.command(name="join-history", description="See who approved/declined recent WhatsApp join requests (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def join_history_cmd(interaction: discord.Interaction):
        with _cursor() as cur:
            cur.execute(
                "SELECT id, name, status, resolved_by_name, resolved_at "
                "FROM join_requests WHERE status IN ('approved', 'denied', 'on_hold') "
                "ORDER BY resolved_at DESC NULLS LAST LIMIT 15"
            )
            rows = cur.fetchall()

        if not rows:
            await interaction.response.send_message("No resolved requests yet.", ephemeral=True)
            return

        lines = []
        for row in rows:
            who = row["resolved_by_name"] or "unknown"
            when = row["resolved_at"].strftime("%b %d, %H:%M") if row["resolved_at"] else "—"
            lines.append(f"#{row['id']} **{row['name']}** — {row['status']} by **{who}** ({when})")

        embed = discord.Embed(title="Recent join-request decisions", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    return client


# ---------- Flask app ----------
app = Flask(__name__)


def check_auth():
    if not WEB_PASSWORD:
        return True
    return request.args.get("pw") == WEB_PASSWORD or request.headers.get("X-PW") == WEB_PASSWORD


@app.route("/")
def index():
    return render_template_string(INDEX_HTML, locked=bool(WEB_PASSWORD))


@app.route("/ping")
def ping():
    """Lightweight health-check for uptime monitors. No auth, no Discord calls."""
    return "pong", 200


@app.route("/api/channels")
def api_channels():
    # (no password required — this is a read-only endpoint)
    try:
        channels = run_coro(list_voice_channels())
        return jsonify({"channels": channels})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lobby_members")
def api_lobby_members():
    # (no password required — this is a read-only endpoint)
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
        last_split["team_a" if team == "a" else "team_b"] = ids
        moved, failed = run_coro(move_members(members, int(target_id)))
        return jsonify({"moved": moved, "failed": failed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    member_ids = body.get("member_ids")
    if not member_ids:
        return jsonify({"error": "member_ids required"}), 400
    try:
        guild = bot.get_guild(GUILD_ID)
        ids = [int(i) for i in member_ids]
        members = [guild.get_member(i) for i in ids]
        members = [m for m in members if m and m.voice is not None]
        if not members:
            return jsonify({"error": "None of the selected players are currently in a voice channel"}), 400
        disconnected, failed = run_coro(disconnect_members(members))
        return jsonify({"disconnected": disconnected, "failed": failed})
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
    # (no password required — this is a read-only endpoint)
    limit = min(int(request.args.get("limit", 50)), 300)
    if is_enabled():
        raw = fetch_logs(limit)
        events = [{
            "time": r["created_at"],
            "action": r["action"],
            "user_id": r["user_id"],
            "name": r["user_name"],
            "avatar": None,  # not persisted — Discord CDN avatar URLs are transient
            "actor_name": r["actor_name"],
            "from": r["from_channel_name"],
            "to": r["to_channel_name"],
            "details": r["details"],
        } for r in raw]
        return jsonify({"events": events, "source": "database"})
    return jsonify({"events": list(voice_log)[:limit], "source": "memory"})


@app.route("/api/toggles", methods=["GET"])
def api_toggles_list():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({
        "toggles": [
            {"key": key, "description": desc, "enabled": get_toggle(key)}
            for key, desc in TOGGLE_KEYS.items()
        ]
    })


@app.route("/api/toggles/<key>", methods=["POST"])
def api_toggles_set(key):
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    if key not in TOGGLE_KEYS:
        return jsonify({"error": "unknown toggle key"}), 400
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    set_toggle(key, enabled)
    return jsonify({"key": key, "enabled": enabled})


@app.route("/api/birthday-reminder-interval", methods=["GET"])
def api_birthday_reminder_interval_get():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    hours = get_birthday_reminder_interval_hours()
    # Pick whichever unit displays the value most cleanly, purely for the UI default
    if hours % (24 * 365) == 0 and hours >= 24 * 365:
        return jsonify({"value": hours / (24 * 365), "unit": "years"})
    if hours % 24 == 0 and hours >= 24:
        return jsonify({"value": hours / 24, "unit": "days"})
    if hours >= 1:
        return jsonify({"value": hours, "unit": "hours"})
    return jsonify({"value": round(hours * 60, 2), "unit": "minutes"})


@app.route("/api/birthday-reminder-interval", methods=["POST"])
def api_birthday_reminder_interval_set():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid value"}), 400
    unit = body.get("unit")
    multiplier = {"minutes": 1 / 60, "hours": 1, "days": 24, "years": 24 * 365}.get(unit)
    if multiplier is None:
        return jsonify({"error": "unit must be minutes, hours, days, or years"}), 400
    if value <= 0:
        return jsonify({"error": "value must be greater than 0"}), 400
    hours = value * multiplier
    set_birthday_reminder_interval_hours(hours)
    return jsonify({"hours": hours})


@app.route("/api/lock", methods=["POST"])
def api_lock():
    """Lock one or more voice channels: members currently inside become restricted
    to their current channel — they can leave and rejoin it, but not switch to
    another locked channel, and no one outside the snapshot can join it."""
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    channel_ids = body.get("channel_ids", [])
    if not channel_ids:
        return jsonify({"error": "channel_ids required"}), 400
    try:
        guild = bot.get_guild(GUILD_ID)
        locked_names = []
        for cid_raw in channel_ids:
            cid = int(cid_raw)
            vc = guild.get_channel(cid)
            if vc is None or not channel_allowed(vc):
                continue
            channel_locks.add(cid)
            save_lock_channel(cid, vc.name)
            locked_names.append(vc.name)
            for member in vc.members:
                locked_members[member.id] = cid
                save_lock_member(member.id, cid)
        log_event(
            action="LOCK_ENABLED",
            details=f"Locked: {', '.join(locked_names)}" if locked_names else "No valid channels selected",
        )
        return jsonify({"locked_channel_ids": [str(c) for c in channel_locks]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    """Unlock specific channels, or all of them if channel_ids is omitted/empty."""
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    channel_ids = body.get("channel_ids")
    try:
        if channel_ids:
            ids = [int(c) for c in channel_ids]
            for cid in ids:
                channel_locks.discard(cid)
            to_remove = [uid for uid, allowed in locked_members.items() if allowed in ids]
            for uid in to_remove:
                locked_members.pop(uid, None)
            remove_lock_channels(ids)
            remove_lock_members_by_channels(ids)
            log_event(action="LOCK_DISABLED", details=f"Unlocked {len(ids)} channel(s)")
        else:
            channel_locks.clear()
            locked_members.clear()
            clear_lock_channels()
            clear_lock_members()
            log_event(action="LOCK_DISABLED", details="Unlocked all channels")
        return jsonify({"locked_channel_ids": [str(c) for c in channel_locks]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lock_status")
def api_lock_status():
    # (no password required — this is a read-only endpoint)
    try:
        guild = bot.get_guild(GUILD_ID)
        channels = []
        for cid in channel_locks:
            vc = guild.get_channel(cid)
            if vc is None:
                continue
            member_count = sum(1 for uid, allowed in locked_members.items() if allowed == cid)
            channels.append({"id": str(cid), "name": vc.name, "locked_member_count": member_count})
        return jsonify({
            "active": len(channel_locks) > 0,
            "channels": channels,
            "total_locked_members": len(locked_members),
            "violation_action": LOCK_VIOLATION_ACTION,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/leaderboard")
def api_leaderboard():
    # (no password required — this is a read-only endpoint)
    if not is_enabled():
        return jsonify({
            "enabled": False,
            "message": "Set DATABASE_URL to enable leaderboards — they need history to compute from.",
        })
    period = request.args.get("period", "all")
    if period not in ("today", "yesterday", "week", "month", "all"):
        period = "all"
    try:
        now = datetime.now(timezone.utc)
        period_start, period_end = vs.period_bounds(period, now=now, tz_offset_hours=TIMEZONE_OFFSET_HOURS)
        raw_sessions = fetch_sessions_for_period(period_start, period_end)
        sessions = vs.clip_sessions_to_period(raw_sessions, period_start, period_end, now=now)

        vc_time = vs.vc_time_totals(sessions)
        longest = vs.longest_sessions(sessions)
        night_early = vs.night_owl_and_early_bird(sessions, tz_offset_hours=TIMEZONE_OFFSET_HOURS)

        def fmt(entries, key="total_seconds"):
            return [{**e, "formatted": vs.format_duration(e[key])} for e in entries]

        result = {
            "enabled": True,
            "period": period,
            "vc_time": fmt(vc_time),
            "longest_session": fmt(longest, key="duration_seconds"),
            "night_owl": (
                {**night_early["night_owl"], "formatted": vs.format_duration(night_early["night_owl"]["seconds"])}
                if night_early["night_owl"] else None
            ),
            "early_bird": (
                {**night_early["early_bird"], "formatted": vs.format_duration(night_early["early_bird"]["seconds"])}
                if night_early["early_bird"] else None
            ),
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/dashboard")
def api_analytics_dashboard():
    # (no password required — this is a read-only endpoint)
    if not is_enabled():
        return jsonify({
            "enabled": False,
            "message": "Set DATABASE_URL to enable the analytics dashboard — it needs history to compute from.",
        })
    period = request.args.get("period", "all")
    if period not in ("today", "yesterday", "week", "month", "all"):
        period = "all"
    try:
        now = datetime.now(timezone.utc)
        period_start, period_end = vs.period_bounds(period, now=now, tz_offset_hours=TIMEZONE_OFFSET_HOURS)
        raw_sessions = fetch_sessions_for_period(period_start, period_end)
        sessions = vs.clip_sessions_to_period(raw_sessions, period_start, period_end, now=now)

        hour_totals = vs.hour_of_day_totals(sessions, tz_offset_hours=TIMEZONE_OFFSET_HOURS)
        channels = vs.channel_totals(sessions)
        peaks = vs.peak_concurrent_by_channel(sessions)
        return jsonify({
            "enabled": True,
            "period": period,
            "hour_of_day_seconds": hour_totals,
            "channel_totals": [{**c, "formatted": vs.format_duration(c["total_seconds"])} for c in channels],
            "peak_concurrent": peaks,
            "total_sessions_analyzed": len(sessions),
            "note": "All-time truly means all history in vc_sessions — no day or row limit.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def start_bot():
    global bot, bot_loop, channel_locks, locked_members
    init_db()
    channel_locks, locked_members = load_locks()
    if channel_locks:
        print(f"[startup] Restored {len(channel_locks)} locked channel(s), {len(locked_members)} locked member(s) from database.", flush=True)
    backfill_vc_sessions_from_logs()

    loop = asyncio.new_event_loop()
    bot_loop = loop
    asyncio.set_event_loop(loop)

    retry_delay = 30    # seconds — conservative starting point so we don't add to a rate limit
    max_delay = 300      # cap backoff at 5 minutes between attempts

    while True:
        bot = make_bot()
        try:
            loop.run_until_complete(bot.start(DISCORD_TOKEN))
            # bot.start() only returns normally if bot.close() was called deliberately —
            # nothing in this app calls that, so reaching here means a clean shutdown was
            # requested elsewhere; don't loop forever retrying in that case.
            print("[bot] Discord connection closed cleanly, not reconnecting.", flush=True)
            break
        except discord.LoginFailure:
            print("[bot] DISCORD_TOKEN is invalid — fix the token and redeploy. Not retrying.", flush=True)
            break
        except discord.HTTPException as e:
            status = getattr(e, "status", None)
            if status == 429:
                print(f"[bot] Discord rate-limited our login (429). Retrying in {retry_delay}s...", flush=True)
            else:
                print(f"[bot] Discord HTTP error during login ({status}): {e}. Retrying in {retry_delay}s...", flush=True)
        except Exception as e:
            print(f"[bot] Unexpected error connecting to Discord: {e}. Retrying in {retry_delay}s...", flush=True)
        finally:
            # A failed login still opens an underlying aiohttp session/connector for
            # that attempt — close it before the next retry, or each attempt leaks one
            # (visible as "Unclosed connector" warnings piling up over time).
            try:
                loop.run_until_complete(bot.close())
            except Exception:
                pass  # bot was never fully initialized enough to close cleanly — nothing to clean up

        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, max_delay)


if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
