/* Page shell: live status pill and the Overview status tiles.
 * Reads GET /api/status. Uses textContent only, so server values can never
 * inject markup. States: loading | online | degraded | stale | offline. */
(function () {
  "use strict";

  var POLL_MS = 30000;
  var pill = document.getElementById("app-status");
  var lastOk = null;              // Date of last good reading
  var lastData = null;
  var shownState = "";

  function $(sel) { return document.querySelector(sel); }
  function bind(name, text) {
    var el = $('[data-bind="' + name + '"]');
    if (el) el.textContent = text;
  }

  function formatUptime(s) {
    var d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    if (d) return d + "d " + h + "h";
    if (h) return h + "h " + m + "m";
    return m + "m";
  }
  function formatOffset(h) {
    var sign = h < 0 ? "-" : "+", a = Math.abs(h);
    var hh = Math.floor(a), mm = Math.round((a - hh) * 60);
    return "UTC" + sign + String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0");
  }
  function ago(date) {
    var s = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (s < 5) return "just now";
    if (s < 60) return s + " s ago";
    return Math.floor(s / 60) + " min ago";
  }

  function setPill(state, text) {
    if (!pill) return;
    if (shownState === state + "|" + text) return;   // announce changes only
    shownState = state + "|" + text;
    pill.setAttribute("data-state", state);
    var t = pill.querySelector(".status-text");
    if (t) t.textContent = text;
  }

  function showError(title, detail) {
    var box = document.getElementById("status-error");
    if (!box) return;
    if (!title) { box.hidden = true; return; }
    document.getElementById("status-error-title").textContent = title;
    document.getElementById("status-error-detail").textContent = detail || "";
    box.hidden = false;
  }

  function renderData(d, stale) {
    var botUp = d.bot && d.bot.ready, dbUp = d.database && d.database.connected;
    bind("bot", botUp ? "Connected" : "Not connected");
    bind("bot-note", botUp ? (d.bot.latency_ms != null ? "Latency " + d.bot.latency_ms + " ms" : "Latency not reported")
                           : "Voice and Discord actions will fail until it reconnects");
    bind("db", dbUp ? "Connected" : "Unavailable");
    bind("db-note", dbUp ? "Reads and writes work" : "Data is not being saved");
    bind("uptime", formatUptime(d.uptime_seconds || 0));
    var prot = d.web && d.web.password_protected;
    bind("access", prot ? "Password set" : "No password");
    bind("access-note", prot ? "Control actions need the panel password" : "Control actions are open to anyone with the link");
    bind("tz", formatOffset(d.timezone_offset_hours || 0));
    bind("tz-note", d.timezone_configured ? "Used for reservation dates and times"
                                          : "Not configured: set TIMEZONE_OFFSET_HOURS. New reservations are blocked.");
    if (lastOk) bind("seen", ago(lastOk) + (stale ? " (stale)" : ""));
  }

  function apply(state, data, err) {
    if (state === "online" || state === "degraded") {
      showError(null);
      renderData(data, false);
      setPill(state, state === "online" ? "All systems running" : (data.bot.ready ? "Database down" : "Bot offline"));
      return;
    }
    // stale or offline: keep the last known values on screen, labelled
    var detail = err && err.message ? err.message : "";
    if (state === "stale" && lastData) {
      renderData(lastData, true);
      showError("Showing old data.", detail + " Last good reading " + ago(lastOk) + ".");
      setPill("stale", "Status is stale");
    } else {
      ["bot", "db", "uptime", "access", "tz", "seen"].forEach(function (k) { bind(k, "—"); });
      bind("bot-note", "Unknown"); bind("db-note", "Unknown"); bind("uptime-note", "Unknown");
      bind("access-note", "Unknown"); bind("tz-note", "Unknown"); bind("seen-note", "No reading yet");
      showError("Status unavailable.", detail);
      setPill("offline", "Cannot reach server");
    }
  }

  function poll() {
    if (!window.VC || !window.VC.api) return;
    window.VC.api.get("/api/status").then(function (d) {
      lastOk = new Date();
      lastData = d;
      document.dispatchEvent(new CustomEvent("vc:status", { detail: d }));
      apply(d.overall === "ok" ? "online" : "degraded", d);
      bind("seen", ago(lastOk));
    }, function (err) {
      // A failed poll after an earlier good reading keeps the old values, labelled stale.
      apply(lastData ? "stale" : "offline", null, err);
      document.dispatchEvent(new CustomEvent("vc:status-failed", { detail: err }));
    });
  }

  window.addEventListener("online", poll);
  window.addEventListener("offline", function () { apply(lastData ? "stale" : "offline", null, { message: "You are offline." }); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });

  poll();
  setInterval(function () { if (!document.hidden) poll(); }, POLL_MS);
  setInterval(function () { if (lastOk && lastData && !document.hidden) bind("seen", ago(lastOk)); }, 10000);

  /* Small toast helper for later phases. */
  window.VC = window.VC || {};
  window.VC.toast = function (message, kind) {
    var host = document.getElementById("toasts");
    if (!host) return;
    var el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("data-kind", kind || "info");
    el.textContent = String(message);
    host.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 5000);
  };
})();
