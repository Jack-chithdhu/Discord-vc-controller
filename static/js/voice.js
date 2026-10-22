/* Voice Operations page. Uses only the existing endpoints; nothing here widens
 * what the server allows.
 *
 *   read (no password):  GET /api/channels, /api/lobby_members, /api/lock_status
 *   needs the password:  POST /api/split, /api/move/<a|b>, /api/rematch, /api/regroup,
 *                        /api/disconnect, /api/voice_toggle, /api/lock, /api/unlock
 *
 * Rules this page follows:
 *  - A button is only enabled when the action can really run, and a request that
 *    is in flight disables every action, so nothing is sent twice.
 *  - Anything that moves, disconnects or locks people asks first, naming who and where.
 *  - "Done" is only shown after the server says so; partial failures are listed.
 *  - The server decides who may act. A 401 re-locks the page.
 */
(function () {
  "use strict";
  var VC = window.VC, ui = VC.ui, api = VC.api;
  if (!document.getElementById("voice-root")) return;

  var $ = function (id) { return document.getElementById(id); };
  var REFRESH_MS = 10000;
  var SEL_KEY = "vc.voice.selection";

  var channels = [];            // [{id,name,member_count,locked}]
  var players = [];             // members of the lobby
  var assign = {};              // member id -> "a" | "b" (absent = unassigned)
  var lockInfo = null;          // /api/lock_status
  var busy = false;
  var loaded = { channels: false, players: false };
  var sig = { channels: "", players: "" };    // re-render only when data changed, so focus and open menus are not disturbed

  var els = {
    lobby: $("sel-lobby"), a: $("sel-a"), b: $("sel-b"),
    players: $("players"), playersNote: $("players-note"),
    result: $("voice-result"), dataNote: $("voice-data-note"),
    gate: $("voice-gate"), open: $("voice-open"),
    lockList: $("lock-list"), lockSummary: $("lock-summary"),
    confirm: $("confirm-dialog"), confirmTitle: $("confirm-title"), confirmBody: $("confirm-body"),
    confirmOk: $("confirm-ok"), confirmCancel: $("confirm-cancel")
  };

  // ---------------------------------------------------------------- helpers
  function chan(id) { for (var i = 0; i < channels.length; i++) if (channels[i].id === id) return channels[i]; return null; }
  function name(id) { var c = chan(id); return c ? c.name : "that channel"; }
  function assigned(team) { return players.filter(function (p) { return assign[p.id] === team; }); }

  function say(kind, title, lines) {
    els.result.textContent = "";
    var box = ui.el("div", "banner " + (kind === "error" ? "banner--warning" : kind === "success" ? "banner--info" : "banner--info"));
    var inner = ui.el("div");
    inner.appendChild(ui.el("strong", null, title));
    (lines || []).forEach(function (l) { inner.appendChild(ui.el("div", "small", l)); });
    box.appendChild(inner);
    els.result.appendChild(box);
  }

  function describeOutcome(moved, failed, verb) {
    var lines = [];
    if (moved && moved.length) lines.push(verb + ": " + moved.map(function (m) { return m.name; }).join(", "));
    if (failed && failed.length) lines.push("Could not move: " + failed.map(function (f) { return f.name + " (" + f.reason + ")"; }).join("; "));
    return lines;
  }

  function saveSel() {
    try { window.sessionStorage.setItem(SEL_KEY, JSON.stringify({ lobby: els.lobby.value, a: els.a.value, b: els.b.value })); } catch (e) { /* optional */ }
  }
  function loadSel() {
    try { return JSON.parse(window.sessionStorage.getItem(SEL_KEY) || "{}") || {}; } catch (e) { return {}; }
  }

  // ---------------------------------------------------------------- confirmation
  function confirmAction(title, body, okLabel, danger) {
    if (!els.confirm || typeof els.confirm.showModal !== "function") return Promise.resolve(window.confirm(title + "\n" + body));
    els.confirmTitle.textContent = title;
    els.confirmBody.textContent = body;
    els.confirmOk.textContent = okLabel;
    els.confirmOk.className = "btn " + (danger ? "btn--danger" : "btn--primary");
    return new Promise(function (resolve) {
      function done(v) { els.confirm.removeEventListener("close", onClose); els.confirmOk.onclick = null; els.confirmCancel.onclick = null; resolve(v); }
      var result = false;
      function onClose() { done(result); }
      els.confirmOk.onclick = function () { result = true; els.confirm.close(); };
      els.confirmCancel.onclick = function () { result = false; els.confirm.close(); };
      els.confirm.addEventListener("close", onClose);
      els.confirm.showModal();
      els.confirmCancel.focus();         // the safe choice is focused first
    });
  }

  // ---------------------------------------------------------------- one place that sends privileged requests
  function send(path, body) {
    busy = true; syncControls();
    return api.post(path, body, { auth: true }).then(function (d) { busy = false; syncControls(); return d; }, function (err) {
      busy = false;
      if (err && err.kind === "unauthorized") VC.access.expire();
      syncControls();
      throw err;
    });
  }
  function failed(title, err) { say("error", title, [ui.describeError(err)]); }

  // ---------------------------------------------------------------- rendering
  function fillSelect(sel, keep, placeholder) {
    var prev = keep !== undefined ? keep : sel.value;
    sel.textContent = "";
    var o = ui.el("option", null, placeholder); o.value = ""; sel.appendChild(o);
    channels.forEach(function (c) {
      var op = ui.el("option", null, c.name + " (" + c.member_count + ")" + (c.locked ? " · locked" : ""));
      op.value = c.id; sel.appendChild(op);
    });
    sel.value = chan(prev) ? prev : "";
  }

  function renderChannels() {
    fillSelect(els.lobby, els.lobby.value, "Choose the lobby channel…");
    fillSelect(els.a, els.a.value, "Choose the Team A channel…");
    fillSelect(els.b, els.b.value, "Choose the Team B channel…");
    renderLockList();
  }

  function teamRadio(p, value, label) {
    var lab = ui.el("label", "seg-opt");
    var inp = document.createElement("input");
    inp.type = "radio"; inp.name = "team-" + p.id; inp.value = value;
    inp.checked = (assign[p.id] || "") === value;
    inp.addEventListener("change", function () { if (value) assign[p.id] = value; else delete assign[p.id]; renderCounts(); syncControls(); });
    lab.appendChild(inp);
    lab.appendChild(ui.el("span", null, label));
    return lab;
  }

  function renderPlayers() {
    els.players.textContent = "";
    if (!els.lobby.value) { els.players.appendChild(ui.el("li", "state", "Choose the lobby channel to see who is in it.")); renderCounts(); return; }
    if (!players.length) { els.players.appendChild(ui.el("li", "state", "Nobody is in this channel right now.")); renderCounts(); return; }
    players.forEach(function (p) {
      var li = ui.el("li", "player");
      var who = ui.el("div", "player-who");
      who.appendChild(ui.avatar(p.name, p.id));
      who.appendChild(ui.el("span", "player-name", p.name));
      if (p.muted) who.appendChild(ui.el("span", "badge badge--warning", "Muted"));
      if (p.deafened) who.appendChild(ui.el("span", "badge badge--warning", "Deafened"));
      li.appendChild(who);

      var seg = ui.el("fieldset", "seg");
      seg.appendChild(ui.el("legend", "sr-only", "Team for " + p.name));
      seg.appendChild(teamRadio(p, "", "None"));
      seg.appendChild(teamRadio(p, "a", "A"));
      seg.appendChild(teamRadio(p, "b", "B"));
      li.appendChild(seg);

      var acts = ui.el("div", "player-actions");
      acts.appendChild(actionBtn(p.muted ? "Unmute" : "Mute", "mute-" + p.id, function () { toggle(p, "mute"); }, p.name));
      acts.appendChild(actionBtn(p.deafened ? "Undeafen" : "Deafen", "deaf-" + p.id, function () { toggle(p, "deafen"); }, p.name));
      acts.appendChild(actionBtn("Disconnect", "dc-" + p.id, function () { disconnect(p); }, p.name, true));
      li.appendChild(acts);
      els.players.appendChild(li);
    });
    renderCounts();
  }

  function actionBtn(label, key, fn, who, danger) {
    var b = ui.el("button", "btn btn--sm" + (danger ? " btn--danger" : ""), label);
    b.type = "button"; b.setAttribute("data-auth", "1"); b.setAttribute("aria-label", label + " " + who);
    b.addEventListener("click", fn);
    return b;
  }

  function renderCounts() {
    $("count-a").textContent = String(assigned("a").length);
    $("count-b").textContent = String(assigned("b").length);
  }

  function renderLockList() {
    var ticked = {};
    Array.prototype.forEach.call(document.querySelectorAll(".lock-cb:checked"), function (c) { ticked[c.value] = true; });
    els.lockList.textContent = "";
    if (!channels.length) { els.lockList.appendChild(ui.el("li", "muted", "No voice channels.")); return; }
    channels.forEach(function (c) {
      var li = ui.el("li", "lock-row");
      if (c.locked) {
        var left = ui.el("div", "grow");
        left.appendChild(ui.el("strong", null, c.name));
        left.appendChild(ui.el("div", "small muted", c.member_count + " in channel"));
        li.appendChild(left);
        li.appendChild(ui.el("span", "badge badge--warning", "Locked"));
        li.appendChild(actionBtn("Unlock", "unlock-" + c.id, function () { unlock([c.id], c.name); }, c.name));
      } else {
        var lab = ui.el("label", "check grow");
        var cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = c.id; cb.className = "lock-cb"; cb.checked = !!ticked[c.id];
        cb.addEventListener("change", syncControls);
        lab.appendChild(cb);
        var t = ui.el("span"); t.appendChild(ui.el("strong", null, c.name)); t.appendChild(document.createTextNode(" · " + c.member_count + " in channel"));
        lab.appendChild(t);
        li.appendChild(lab);
      }
      els.lockList.appendChild(li);
    });
  }

  function renderLockSummary() {
    if (!lockInfo) { els.lockSummary.textContent = "Lock status is not available."; return; }
    els.lockSummary.textContent = lockInfo.active
      ? lockInfo.channels.length + " channel(s) locked, " + lockInfo.total_locked_members + " member(s) restricted. If someone breaks a lock the bot will: " + lockInfo.violation_action + "."
      : "Nothing is locked.";
  }

  // The one function that decides what is enabled.
  function syncControls() {
    var can = VC.access.canAct();
    var lobby = els.lobby.value, A = els.a.value, B = els.b.value;
    var hasA = assigned("a").length > 0, hasB = assigned("b").length > 0;
    var distinct = A && B && A !== B;
    var anyLockPick = !!document.querySelector(".lock-cb:checked");

    function set(id, ok, why) {
      var b = $(id); if (!b) return;
      b.disabled = !(ok && can && !busy);
      if (!ok && why) b.title = why; else b.removeAttribute("title");
    }
    set("btn-split", !!lobby && players.length >= 2, "Pick a lobby with at least 2 people");
    set("btn-move-a", hasA && !!A, "Assign players to Team A and pick its channel");
    set("btn-move-b", hasB && !!B, "Assign players to Team B and pick its channel");
    set("btn-swap", !!distinct, "Pick two different team channels");
    set("btn-regroup", !!distinct && !!lobby && lobby !== A && lobby !== B, "Pick a lobby that differs from both team channels");
    set("btn-lock", anyLockPick, "Tick at least one channel");
    set("btn-unlock-all", !!(lockInfo && lockInfo.active), "Nothing is locked");
    document.querySelectorAll("[data-auth]").forEach(function (b) { b.disabled = !can || busy; });

    var state = VC.access.state();
    els.gate.hidden = !(state === "locked" || state === "checking");
    els.open.hidden = state !== "open";
    document.getElementById("voice-root").setAttribute("aria-busy", busy ? "true" : "false");
  }

  // ---------------------------------------------------------------- loading
  function loadChannels() {
    return api.get("/api/channels").then(function (d) {
      channels = d.channels || []; loaded.channels = true; els.dataNote.hidden = true;
      var s = JSON.stringify(channels);
      if (s !== sig.channels) { sig.channels = s; renderChannels(); }
    }, function (err) {
      if (loaded.channels) { els.dataNote.hidden = false; els.dataNote.textContent = "Showing old channel data. " + ui.describeError(err); }
      else { say("error", "Channels could not be loaded", [ui.describeError(err), "The bot may still be connecting. Try Refresh in a moment."]); }
    });
  }

  function loadPlayers() {
    if (!els.lobby.value) { players = []; assign = {}; sig.players = ""; renderPlayers(); return Promise.resolve(); }
    return api.get("/api/lobby_members?lobby_id=" + encodeURIComponent(els.lobby.value)).then(function (d) {
      players = d.members || [];
      var ids = {}; players.forEach(function (p) { ids[p.id] = true; });
      Object.keys(assign).forEach(function (id) { if (!ids[id]) delete assign[id]; });
      loaded.players = true; els.playersNote.textContent = "Updated " + new Date().toLocaleTimeString() + ". Refreshes every 10 seconds.";
      var s = JSON.stringify(players.map(function (p) { return [p.id, p.name, p.muted, p.deafened]; }));
      if (s !== sig.players) { sig.players = s; renderPlayers(); }
    }, function (err) {
      els.playersNote.textContent = (loaded.players ? "Showing old player data. " : "") + ui.describeError(err);
      if (!loaded.players) { players = []; renderPlayers(); }
    });
  }

  function loadLock() {
    return api.get("/api/lock_status").then(function (d) { lockInfo = d; renderLockSummary(); }, function () { lockInfo = null; renderLockSummary(); });
  }

  function refreshAll() {
    return Promise.all([loadChannels(), loadLock()]).then(loadPlayers).then(syncControls);
  }

  // ---------------------------------------------------------------- actions
  function split() {
    send("/api/split", { lobby_id: els.lobby.value }).then(function (d) {
      assign = {};
      d.team_a.forEach(function (m) { assign[m.id] = "a"; });
      d.team_b.forEach(function (m) { assign[m.id] = "b"; });
      renderPlayers();
      syncControls();
      say("success", "Players divided: Team A " + d.team_a.length + ", Team B " + d.team_b.length, ["Nobody has been moved yet. Choose the team channels, then move each team."]);
    }, function (err) { failed("Could not split the lobby", err); });
  }

  function moveTeam(team) {
    var who = assigned(team), target = els[team].value, label = "Team " + team.toUpperCase();
    confirmAction("Move " + label + "?", "Move " + who.length + " player(s) to “" + name(target) + "”: " + who.map(function (p) { return p.name; }).join(", ") + ".", "Move players", false).then(function (ok) {
      if (!ok) return;
      send("/api/move/" + team, { channel_id: target, member_ids: who.map(function (p) { return p.id; }) }).then(function (d) {
        var lines = describeOutcome(d.moved, d.failed, "Moved");
        say(d.failed && d.failed.length ? "error" : "success", d.failed && d.failed.length ? label + ": some players could not be moved" : label + " moved", lines);
        refreshAll();
      }, function (err) { failed("Could not move " + label, err); });
    });
  }

  function swap() {
    confirmAction("Swap sides?", "Everyone in “" + name(els.a.value) + "” goes to “" + name(els.b.value) + "”, and everyone in “" + name(els.b.value) + "” goes to “" + name(els.a.value) + "”.", "Swap sides", false).then(function (ok) {
      if (!ok) return;
      send("/api/rematch", { team_a_channel: els.a.value, team_b_channel: els.b.value }).then(function (d) {
        say(d.failed && d.failed.length ? "error" : "success", "Sides swapped", (d.failed || []).map(function (f) { return "Could not move " + f.name + " (" + f.reason + ")"; }));
        refreshAll();
      }, function (err) { failed("Could not swap sides", err); });
    });
  }

  function regroup() {
    confirmAction("Regroup to the lobby?", "Everyone in “" + name(els.a.value) + "” and “" + name(els.b.value) + "” goes to “" + name(els.lobby.value) + "”.", "Regroup", false).then(function (ok) {
      if (!ok) return;
      send("/api/regroup", { from_channel_ids: [els.a.value, els.b.value], to_channel_id: els.lobby.value }).then(function (d) {
        say(d.failed && d.failed.length ? "error" : "success", "Regrouped in the lobby", describeOutcome(d.moved, d.failed, "Moved"));
        refreshAll();
      }, function (err) { failed("Could not regroup", err); });
    });
  }

  function disconnect(p) {
    confirmAction("Disconnect " + p.name + "?", p.name + " will be removed from their voice channel. They can rejoin.", "Disconnect", true).then(function (ok) {
      if (!ok) return;
      send("/api/disconnect", { member_ids: [p.id] }).then(function (d) {
        var f = d.failed || [];
        say(f.length ? "error" : "success", f.length ? "Could not disconnect " + p.name : p.name + " was disconnected", f.map(function (x) { return x.name + " (" + x.reason + ")"; }));
        refreshAll();
      }, function (err) { failed("Could not disconnect " + p.name, err); });
    });
  }

  function toggle(p, action) {
    send("/api/voice_toggle", { member_id: p.id, action: action }).then(function (d) {
      say("success", p.name + (action === "mute" ? (d.state ? " was muted" : " was unmuted") : (d.state ? " was deafened" : " was undeafened")), []);
      loadPlayers().then(syncControls);
    }, function (err) { failed("Could not change " + p.name, err); });
  }

  function lockSelected() {
    var picks = Array.prototype.map.call(document.querySelectorAll(".lock-cb:checked"), function (c) { return c.value; });
    confirmAction("Lock " + picks.length + " channel(s)?", picks.map(name).join(", ") + ". Members inside now can leave and rejoin that channel but cannot switch to another locked one, and nobody else can join it.", "Lock channels", false).then(function (ok) {
      if (!ok) return;
      send("/api/lock", { channel_ids: picks }).then(function () {
        say("success", "Locked " + picks.length + " channel(s)", []);
        refreshAll();
      }, function (err) { failed("Could not lock", err); });
    });
  }

  function unlock(ids, label) {
    var all = !ids;
    confirmAction(all ? "Unlock all channels?" : "Unlock " + label + "?", all ? "Every locked channel is released." : "This channel is released.", "Unlock", false).then(function (ok) {
      if (!ok) return;
      send("/api/unlock", all ? {} : { channel_ids: ids }).then(function () {
        say("success", all ? "All channels unlocked" : label + " unlocked", []);
        refreshAll();
      }, function (err) { failed("Could not unlock", err); });
    });
  }

  // ---------------------------------------------------------------- wiring
  $("btn-split").addEventListener("click", split);
  $("btn-move-a").addEventListener("click", function () { moveTeam("a"); });
  $("btn-move-b").addEventListener("click", function () { moveTeam("b"); });
  $("btn-swap").addEventListener("click", swap);
  $("btn-regroup").addEventListener("click", regroup);
  $("btn-lock").addEventListener("click", lockSelected);
  $("btn-unlock-all").addEventListener("click", function () { unlock(null); });
  $("btn-refresh").addEventListener("click", function () { refreshAll(); });
  $("btn-gate-unlock").addEventListener("click", function () { VC.access.open(); });
  els.lobby.addEventListener("change", function () { saveSel(); assign = {}; sig.players = ""; loadPlayers().then(syncControls); });
  [els.a, els.b].forEach(function (s) { s.addEventListener("change", function () { saveSel(); syncControls(); }); });
  VC.access.onChange(syncControls);

  var saved = loadSel();
  refreshAll().then(function () {
    ["lobby", "a", "b"].forEach(function (k) { if (saved[k] && chan(saved[k])) els[k].value = saved[k]; });
    return loadPlayers();
  }).then(syncControls);
  syncControls();

  setInterval(function () {
    if (document.hidden || busy || (els.confirm && els.confirm.open)) return;
    Promise.all([loadChannels(), loadLock()]).then(loadPlayers).then(syncControls);
  }, REFRESH_MS);
})();
