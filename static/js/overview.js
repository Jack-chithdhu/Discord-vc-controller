/* Overview page: voice activity from the existing read-only endpoints.
 *   /api/channels                       counts per voice channel
 *   /api/analytics/dashboard?period=today   voice time today
 *   /api/voice_log?limit=8               latest events
 * Each block handles loading, empty, error and stale on its own, so one failing
 * endpoint never blanks the rest. */
(function () {
  "use strict";
  var ui = window.VC.ui, api = window.VC.api;
  var REFRESH_MS = 30000;
  var root = document.getElementById("voice-activity");
  if (!root) return;

  var refreshBtn = document.getElementById("ov-refresh");
  var note = document.getElementById("ov-note");
  var lastGood = { channels: null, today: null, log: null };

  function bind(name, text) { var n = document.querySelector('[data-ov="' + name + '"]'); if (n) n.textContent = text; }

  function renderChannels(list, stale) {
    var people = 0, locked = 0;
    list.forEach(function (c) { people += c.member_count || 0; if (c.locked) locked += 1; });
    bind("people", String(people));
    bind("channels", String(list.length));
    bind("locked", String(locked));
    var box = document.getElementById("ov-channels");
    box.textContent = "";
    if (!list.length) { box.appendChild(ui.el("p", "muted", "No voice channels found.")); return; }
    var ul = ui.el("ul", "caps");
    list.forEach(function (c) {
      var li = ui.el("li", "cap");
      var left = ui.el("div");
      left.appendChild(ui.el("div", "cap-name", c.name));
      left.appendChild(ui.el("div", "cap-note", (c.member_count || 0) + (c.member_count === 1 ? " person" : " people")));
      li.appendChild(left);
      if (c.locked) li.appendChild(ui.el("span", "badge badge--warning", "Locked"));
      ul.appendChild(li);
    });
    box.appendChild(ul);
    if (stale) box.appendChild(ui.el("p", "small muted", "Showing the last data received."));
  }

  function renderLog(events, stale) {
    var box = document.getElementById("ov-log");
    box.textContent = "";
    if (!events.length) { box.appendChild(ui.el("p", "muted", "No activity recorded yet.")); return; }
    var ul = ui.el("ul", "log");
    events.forEach(function (e) { ul.appendChild(ui.logRow(e)); });
    box.appendChild(ul);
    if (stale) box.appendChild(ui.el("p", "small muted", "Showing the last data received."));
  }

  function failBlock(boxId, label, err) {
    var box = document.getElementById(boxId);
    box.textContent = "";
    var s = ui.el("div", "state state--error");
    s.setAttribute("role", "alert");
    s.appendChild(ui.el("span", "state-title", label + " could not be loaded"));
    s.appendChild(ui.el("span", null, ui.describeError(err)));
    box.appendChild(s);
  }

  function load() {
    if (refreshBtn) refreshBtn.disabled = true;
    var problems = [];

    var pChannels = api.get("/api/channels").then(function (d) {
      lastGood.channels = d.channels || [];
      renderChannels(lastGood.channels, false);
    }, function (err) {
      problems.push("channels");
      if (lastGood.channels) renderChannels(lastGood.channels, true);
      else { ["people", "channels", "locked"].forEach(function (k) { bind(k, "—"); }); failBlock("ov-channels", "Channels", err); }
    });

    var pToday = api.get("/api/analytics/dashboard?period=today").then(function (d) {
      lastGood.today = d;
      if (d && d.enabled && Array.isArray(d.hour_of_day_seconds)) {
        bind("today", ui.fmtDuration(d.hour_of_day_seconds.reduce(function (a, b) { return a + b; }, 0)));
        bind("today-note", "Total voice time across everyone, so far today");
      } else {
        bind("today", "—");
        bind("today-note", (d && d.note) || "Analytics are not available");
      }
    }, function () {
      problems.push("voice time");
      if (!lastGood.today) { bind("today", "—"); bind("today-note", "Could not be loaded"); }
    });

    var pLog = api.get("/api/voice_log?limit=8").then(function (d) {
      lastGood.log = d.events || [];
      renderLog(lastGood.log, false);
    }, function (err) {
      problems.push("activity");
      if (lastGood.log) renderLog(lastGood.log, true);
      else failBlock("ov-log", "Activity", err);
    });

    Promise.all([pChannels, pToday, pLog]).then(function () {
      if (refreshBtn) refreshBtn.disabled = false;
      if (note) note.textContent = problems.length
        ? "Some data could not be refreshed (" + problems.join(", ") + "). Older values are labelled."
        : "Updated " + new Date().toLocaleTimeString();
    });
  }

  if (refreshBtn) refreshBtn.addEventListener("click", load);
  load();
  setInterval(function () { if (!document.hidden) load(); }, REFRESH_MS);
})();
