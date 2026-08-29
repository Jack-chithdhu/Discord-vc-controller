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
import random
import asyncio
import threading
from collections import deque, defaultdict
from datetime import datetime, timezone
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from psycopg2 import pool

import discord
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
  #pwbox { display: {{ 'block' if locked else 'none' }}; margin-bottom: 16px; }
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
</head>
<body>
  <h1>VC control</h1>
  <div class="sub">Manage your Valorant voice channels</div>

  <div id="pwbox">
    <input type="password" id="pw" placeholder="Server password" onkeydown="if(event.key==='Enter') unlock()">
    <button class="btn primary" style="margin-top:8px;" onclick="unlock()">Unlock</button>
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

  <div class="tabbar">
    <div class="tab active" data-tab="dashboard" onclick="switchTab('dashboard')"><span class="tab-icon">📊</span>Dashboard</div>
    <div class="tab" data-tab="teams" onclick="switchTab('teams')"><span class="tab-icon">🎮</span>Teams</div>
    <div class="tab" data-tab="lock" onclick="switchTab('lock')"><span class="tab-icon">🔒</span>Lock</div>
    <div class="tab" data-tab="stats" onclick="switchTab('stats')"><span class="tab-icon">🏆</span>Stats</div>
    <div class="tab" data-tab="log" onclick="switchTab('log')"><span class="tab-icon">📋</span>Log</div>
  </div>

<script>
const resultEl = document.getElementById('result');
let channels = [];
let players = [];
let assignments = {};
let currentTab = 'dashboard';
let currentPeriod = 'all';
let dragState = null;

function pw() { const el = document.getElementById('pw'); return el ? el.value : ''; }

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
  refreshCurrentTab();
}

async function refreshCurrentTab() {
  if (currentTab === 'dashboard') { loadDashboardStats(); }
  else if (currentTab === 'teams') { loadChannels(); loadPlayers(); }
  else if (currentTab === 'lock') { loadLockPanel(); }
  else if (currentTab === 'stats') { loadLeaderboard(); loadDashboard(); }
  else if (currentTab === 'log') { loadLog(); }
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
  html += `<div style="font-size:11px;color:var(--muted);margin-top:4px;">Based on the last 90 days of logged activity (${data.total_events_analyzed} events).</div>`;
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
        print("[db] DATABASE_URL not set — logs and locks will NOT persist across restarts.")
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
        print("[db] PostgreSQL ready (vc_logs, vc_lock_channels, vc_lock_members).")
        return True
    except Exception as e:
        print(f"[db] Failed to connect/initialize PostgreSQL: {e}. Falling back to in-memory only.")
        _pool = None
        return False


def is_enabled():
    return _pool is not None


@contextmanager
def _cursor():
    conn = _pool.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# ---------- Logs ----------

def log_event(action, user_id=None, user_name=None, actor_id=None, actor_name=None,
              from_channel_id=None, from_channel_name=None,
              to_channel_id=None, to_channel_name=None, details=None):
    if not _pool:
        return
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO vc_logs
                    (action, user_id, user_name, actor_id, actor_name,
                     from_channel_id, from_channel_name, to_channel_id, to_channel_name, details)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (action, user_id, user_name, actor_id, actor_name,
                  from_channel_id, from_channel_name, to_channel_id, to_channel_name, details))
    except Exception as e:
        print(f"[db] log_event failed: {e}")


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
# ANALYTICS (VC-time leaderboards & dashboard math)
# ============================================================
def _parse_time(t):
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def reconstruct_sessions(events, now=None):
    """
    events: list of dicts with action, user_id, user_name, from_channel_name,
            to_channel_name, created_at — ordered ASCENDING by time.
    now: current time to close any still-open sessions against (defaults to utcnow).

    Returns a list of session dicts:
        {user_id, user_name, channel_name, start, end, duration_seconds}

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
        t = _parse_time(e["created_at"])

        if action == "JOINED":
            # Defensive: if somehow already "open" (missed a close event), end it here first.
            if uid in open_sessions:
                sessions.append(_close(open_sessions.pop(uid), t))
            open_sessions[uid] = {"channel_name": e.get("to_channel_name"), "start": t, "user_name": uname}

        elif action == "MOVED":
            if uid in open_sessions:
                sessions.append(_close(open_sessions.pop(uid), t))
            open_sessions[uid] = {"channel_name": e.get("to_channel_name"), "start": t, "user_name": uname}

        elif action in ("LEFT", "DISCONNECTED"):
            if uid in open_sessions:
                sessions.append(_close(open_sessions.pop(uid), t))
            # else: closing event with no matching open session (log history started mid-session) — ignore

    # Close anything still open as of `now`, so live/ongoing sessions count toward totals.
    for uid, s in open_sessions.items():
        sessions.append(_close(s, now))

    return sessions


def _close(open_session, end_time):
    start = open_session["start"]
    duration = max(0.0, (end_time - start).total_seconds())
    return {
        "user_id": None,  # filled by caller if needed; kept out of open_session dict key collision
        "user_name": open_session["user_name"],
        "channel_name": open_session["channel_name"],
        "start": start,
        "end": end_time,
        "duration_seconds": duration,
    }


def reconstruct_sessions_with_ids(events, now=None):
    """Same as reconstruct_sessions but keeps user_id on each session (needed for grouping)."""
    now = now or datetime.now(timezone.utc)
    open_sessions = {}
    sessions = []

    for e in events:
        action = (e.get("action") or "").upper()
        if action == "LOCK_VIOLATION":
            continue
        uid = e["user_id"]
        uname = e.get("user_name") or "Unknown"
        t = _parse_time(e["created_at"])

        if action in ("JOINED", "MOVED"):
            if uid in open_sessions:
                sessions.append({**_close(open_sessions.pop(uid), t), "user_id": uid})
            open_sessions[uid] = {"channel_name": e.get("to_channel_name"), "start": t, "user_name": uname}
        elif action in ("LEFT", "DISCONNECTED"):
            if uid in open_sessions:
                sessions.append({**_close(open_sessions.pop(uid), t), "user_id": uid})

    for uid, s in open_sessions.items():
        sessions.append({**_close(s, now), "user_id": uid})

    return sessions


def _filter_period(sessions, period, now=None):
    now = now or datetime.now(timezone.utc)
    if period == "all":
        return sessions
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = cutoff.fromordinal(cutoff.toordinal() - cutoff.weekday())
    elif period == "month":
        cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return sessions
    return [s for s in sessions if s["end"] >= cutoff]


def vc_time_totals(sessions, period="all", now=None, top=10):
    """Total VC time per user within the period, sorted descending."""
    filtered = _filter_period(sessions, period, now)
    totals = defaultdict(lambda: {"user_name": None, "total_seconds": 0.0})
    for s in filtered:
        entry = totals[s["user_id"]]
        entry["user_name"] = s["user_name"]
        entry["total_seconds"] += s["duration_seconds"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1]["total_seconds"], reverse=True)
    return [{"user_id": uid, **v} for uid, v in ranked[:top]]


def longest_sessions(sessions, period="all", now=None, top=10):
    """The single longest session per user within the period, sorted descending."""
    filtered = _filter_period(sessions, period, now)
    best = {}
    for s in filtered:
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
    """
    Seconds of a session that fall within a daily hour window [start, end).
    Handles windows that wrap past midnight (e.g. 22 -> 4) by checking each
    calendar day the session spans, one day at a time.
    """
    from datetime import timedelta
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
            seg_start = max(cur, w_start)
            seg_end = min(day_slice_end, w_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()
        else:
            # wraps midnight: e.g. 22:00 -> 04:00 next day. Split into [start_hour, 24:00) and [0:00, end_hour).
            w1_start = day_start + timedelta(hours=window_start_hour)
            w1_end = next_day_start
            seg_start = max(cur, w1_start)
            seg_end = min(day_slice_end, w1_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()

            w2_start = day_start
            w2_end = day_start + timedelta(hours=window_end_hour)
            seg_start = max(cur, w2_start)
            seg_end = min(day_slice_end, w2_end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds()

        cur = day_slice_end

    return total


def night_owl_and_early_bird(sessions, period="all", now=None):
    """
    Night owl: most total time logged between 22:00-04:00.
    Early bird: most total time logged between 05:00-09:00.
    Returns {"night_owl": {...} | None, "early_bird": {...} | None}
    """
    filtered = _filter_period(sessions, period, now)
    night_totals = defaultdict(lambda: {"user_name": None, "seconds": 0.0})
    morning_totals = defaultdict(lambda: {"user_name": None, "seconds": 0.0})

    for s in filtered:
        uid = s["user_id"]
        night_totals[uid]["user_name"] = s["user_name"]
        night_totals[uid]["seconds"] += _overlap_seconds(s, 22, 4)
        morning_totals[uid]["user_name"] = s["user_name"]
        morning_totals[uid]["seconds"] += _overlap_seconds(s, 5, 9)

    def top1(totals):
        candidates = [(uid, v) for uid, v in totals.items() if v["seconds"] > 0]
        if not candidates:
            return None
        uid, v = max(candidates, key=lambda kv: kv[1]["seconds"])
        return {"user_id": uid, "user_name": v["user_name"], "seconds": v["seconds"]}

    return {"night_owl": top1(night_totals), "early_bird": top1(morning_totals)}


def hour_of_day_totals(sessions, period="all", now=None):
    """Total combined VC-seconds per hour-of-day (0-23), across all users. For a bar chart."""
    from datetime import timedelta
    filtered = _filter_period(sessions, period, now)
    totals = [0.0] * 24
    for s in filtered:
        cur = s["start"]
        end = s["end"]
        while cur < end:
            hour_start = cur.replace(minute=0, second=0, microsecond=0)
            next_hour = hour_start + timedelta(hours=1)
            seg_end = min(end, next_hour)
            totals[cur.hour] += (seg_end - cur).total_seconds()
            cur = seg_end
    return totals


def channel_totals(sessions, period="all", now=None, top=10):
    """Total combined VC-seconds per channel, across all users."""
    filtered = _filter_period(sessions, period, now)
    totals = defaultdict(float)
    for s in filtered:
        if s["channel_name"]:
            totals[s["channel_name"]] += s["duration_seconds"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [{"channel_name": name, "total_seconds": secs} for name, secs in ranked[:top]]


def peak_concurrent_by_channel(events):
    """
    Peak simultaneous members per channel, via a sweep over join(+1)/leave(-1) events.
    Uses raw events directly (not reconstructed sessions) since this needs the
    actual join/leave timing per channel, not per-user totals.
    """
    channel_deltas = defaultdict(list)  # channel_name -> [(time, +1/-1)]
    open_channel = {}  # user_id -> channel_name currently in

    for e in events:
        action = (e.get("action") or "").upper()
        if action == "LOCK_VIOLATION":
            continue
        uid = e["user_id"]
        t = _parse_time(e["created_at"])

        if action in ("JOINED", "MOVED"):
            prev = open_channel.get(uid)
            if prev:
                channel_deltas[prev].append((t, -1))
            to_ch = e.get("to_channel_name")
            if to_ch:
                channel_deltas[to_ch].append((t, +1))
                open_channel[uid] = to_ch
        elif action in ("LEFT", "DISCONNECTED"):
            prev = open_channel.pop(uid, None)
            if prev:
                channel_deltas[prev].append((t, -1))

    peaks = {}
    for channel, deltas in channel_deltas.items():
        deltas.sort(key=lambda d: d[0])
        running = 0
        peak = 0
        for _, delta in deltas:
            running += delta
            peak = max(peak, running)
        peaks[channel] = peak
    return peaks


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

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True

bot = discord.Client(intents=intents)
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


def run_coro(coro):
    """Run an async discord.py coroutine from Flask's sync thread."""
    future = asyncio.run_coroutine_threadsafe(coro, bot_loop)
    return future.result(timeout=15)


def log_event_full(action, member, from_channel=None, to_channel=None,
                    actor_id=None, actor_name=None, details=None):
    """Write one activity log entry to both the in-memory fallback and PostgreSQL (if configured)."""
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


@bot.event
async def on_ready():
    print(f"Bot ready as {bot.user}")


@bot.event
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
        log_event_full("JOINED", member=member, to_channel=after.channel)
        return

    if before_id is not None and after_id is None:
        actor_id = actor_name = None
        if is_dashboard_action:
            actor_name = "VC Control (Website)"
        else:
            actor = await find_recent_voice_actor(member.guild, "DISCONNECT", before_id)
            if actor:
                actor_id, actor_name = str(actor.id), str(actor)
        log_event_full(
            "DISCONNECTED" if actor_name else "LEFT",
            member=member, from_channel=before.channel,
            actor_id=actor_id, actor_name=actor_name,
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
        log_event_full(
            "MOVED", member=member, from_channel=before.channel, to_channel=after.channel,
            actor_id=actor_id, actor_name=actor_name,
        )


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
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
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
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
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
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    if not is_enabled():
        return jsonify({
            "enabled": False,
            "message": "Set DATABASE_URL to enable leaderboards — they need history to compute from.",
        })
    period = request.args.get("period", "all")
    if period not in ("today", "week", "month", "all"):
        period = "all"
    try:
        events = fetch_events_since(days_back=90)
        sessions = reconstruct_sessions_with_ids(events)
        vc_time = vc_time_totals(sessions, period=period)
        longest = longest_sessions(sessions, period=period)
        night_early = night_owl_and_early_bird(sessions, period=period)

        def fmt(entries, key="total_seconds"):
            return [{**e, "formatted": format_duration(e[key])} for e in entries]

        result = {
            "enabled": True,
            "period": period,
            "vc_time": fmt(vc_time),
            "longest_session": fmt(longest, key="duration_seconds"),
            "night_owl": (
                {**night_early["night_owl"], "formatted": format_duration(night_early["night_owl"]["seconds"])}
                if night_early["night_owl"] else None
            ),
            "early_bird": (
                {**night_early["early_bird"], "formatted": format_duration(night_early["early_bird"]["seconds"])}
                if night_early["early_bird"] else None
            ),
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analytics/dashboard")
def api_analytics_dashboard():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    if not is_enabled():
        return jsonify({
            "enabled": False,
            "message": "Set DATABASE_URL to enable the analytics dashboard — it needs history to compute from.",
        })
    period = request.args.get("period", "all")
    if period not in ("today", "week", "month", "all"):
        period = "all"
    try:
        events = fetch_events_since(days_back=90)
        sessions = reconstruct_sessions_with_ids(events)
        hour_totals = hour_of_day_totals(sessions, period=period)
        channels = channel_totals(sessions, period=period)
        peaks = peak_concurrent_by_channel(events)
        return jsonify({
            "enabled": True,
            "period": period,
            "hour_of_day_seconds": hour_totals,
            "channel_totals": [{**c, "formatted": format_duration(c["total_seconds"])} for c in channels],
            "peak_concurrent": peaks,
            "total_events_analyzed": len(events),
            "note": "Based on the last 90 days of activity, capped at 20,000 events.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def start_bot():
    global bot_loop, channel_locks, locked_members
    init_db()
    channel_locks, locked_members = load_locks()
    if channel_locks:
        print(f"[startup] Restored {len(channel_locks)} locked channel(s), {len(locked_members)} locked member(s) from database.")

    loop = asyncio.new_event_loop()
    bot_loop = loop
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot.start(DISCORD_TOKEN))


if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
