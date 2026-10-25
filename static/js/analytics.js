/* Analytics page: one read-only endpoint, GET /api/analytics/dashboard?period=...
 * Charts follow the dataviz rules: a single series in one hue, bars no thicker
 * than 24px with a 4px rounded top and a square base, hairline grid, a tooltip on
 * hover, a label only on the busiest hour, and a table view of every value. */
(function () {
  "use strict";
  var VC = window.VC, ui = VC.ui, api = VC.api;
  if (!document.getElementById("an-root")) return;
  var $ = function (id) { return document.getElementById(id); };
  var NS = "http://www.w3.org/2000/svg";
  var period = "week", tzOffset = null, last = null, token = 0;

  function bind(k, t) { var n = document.querySelector('[data-an="' + k + '"]'); if (n) n.textContent = t; }
  function svg(tag, attrs) { var n = document.createElementNS(NS, tag); Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); }); return n; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function hourLabel(h) { return pad(h) + ":00–" + pad((h + 1) % 24) + ":00"; }
  function hoursText(sec) { return ui.fmtDuration(sec); }
  function offsetText(h) { var s = h < 0 ? "-" : "+", a = Math.abs(h), hh = Math.floor(a), mm = Math.round((a - hh) * 60); return "UTC" + s + pad(hh) + ":" + pad(mm); }

  /** Round the top of the axis up to a clean number of hours, with at most 4 steps. */
  function niceScale(maxHours) {
    var steps = [0.25, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
    var step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) if (maxHours / steps[i] <= 4) { step = steps[i]; break; }
    return { step: step, top: Math.max(step, Math.ceil(maxHours / step) * step) };
  }
  function tickText(h) { return h === 0 ? "0" : (h < 1 ? Math.round(h * 60) + "m" : h + "h"); }

  // ---------------------------------------------------------------- hour-of-day chart
  function drawHours(seconds) {
    var box = $("an-chart"), wrap = $("an-table-wrap");
    box.textContent = ""; wrap.textContent = "";
    var vals = seconds.map(function (s) { return s / 3600; });
    var max = Math.max.apply(null, vals);
    if (!(max > 0)) { box.appendChild(ui.el("p", "muted", "No voice time recorded in this period.")); bind("busiest", "—"); return; }
    var peak = vals.indexOf(max);
    bind("busiest", hourLabel(peak));

    var W = 720, H = 240, L = 44, R = 8, T = 16, B = 28, iw = W - L - R, ih = H - T - B, slot = iw / 24, bw = Math.min(24, slot - 4);
    var sc = niceScale(max);
    var root = svg("svg", { viewBox: "0 0 " + W + " " + H, class: "chart-svg", role: "img", preserveAspectRatio: "xMidYMid meet",
      "aria-label": "Bar chart of voice time by hour of day. Busiest hour " + hourLabel(peak) + " with " + hoursText(seconds[peak]) + "." });
    function y(v) { return T + ih - (v / sc.top) * ih; }

    for (var t = 0; t <= sc.top + 1e-9; t += sc.step) {                // gridlines + y labels
      root.appendChild(svg("line", { x1: L, x2: W - R, y1: y(t), y2: y(t), class: t === 0 ? "ch-base" : "ch-grid" }));
      var tl = svg("text", { x: L - 8, y: y(t) + 4, "text-anchor": "end", class: "ch-axis" }); tl.textContent = tickText(t); root.appendChild(tl);
    }
    for (var h = 0; h < 24; h++) {
      var x = L + h * slot + (slot - bw) / 2, v = vals[h], bh = (v / sc.top) * ih;
      if (h % 3 === 0) { var xl = svg("text", { x: x + bw / 2, y: H - 8, "text-anchor": "middle", class: "ch-axis" }); xl.textContent = pad(h); root.appendChild(xl); }
      if (bh >= 1) {
        var r = Math.min(4, bh), top = T + ih - bh, base = T + ih;
        root.appendChild(svg("path", { class: "ch-bar" + (h === peak ? " ch-bar--peak" : ""),
          d: "M" + x + "," + base + " L" + x + "," + (top + r) + " Q" + x + "," + top + " " + (x + r) + "," + top +
             " L" + (x + bw - r) + "," + top + " Q" + (x + bw) + "," + top + " " + (x + bw) + "," + (top + r) + " L" + (x + bw) + "," + base + " Z" }));
      }
      var hit = svg("rect", { x: L + h * slot, y: T, width: slot, height: ih + 1, class: "ch-hit", "data-h": h });
      root.appendChild(hit);
    }
    var lab = svg("text", { x: L + peak * slot + slot / 2, y: Math.max(11, y(max) - 6), "text-anchor": "middle", class: "ch-label" });
    lab.textContent = hoursText(seconds[peak]); root.appendChild(lab);        // the one direct label: the extreme

    var tip = ui.el("div", "chart-tip"); tip.hidden = true; tip.setAttribute("role", "presentation");
    box.appendChild(root); box.appendChild(tip);
    function show(ev) {
      var hh = ev.target.getAttribute && ev.target.getAttribute("data-h");
      if (hh === null || hh === undefined) { tip.hidden = true; return; }
      tip.textContent = hourLabel(+hh) + " · " + hoursText(seconds[+hh]);
      tip.hidden = false;
      var b = box.getBoundingClientRect();
      var px = ev.clientX - b.left, py = ev.clientY - b.top;
      tip.style.left = Math.max(4, Math.min(b.width - tip.offsetWidth - 4, px - tip.offsetWidth / 2)) + "px";
      tip.style.top = Math.max(0, py - tip.offsetHeight - 12) + "px";
    }
    root.addEventListener("pointermove", show); root.addEventListener("pointerdown", show);
    root.addEventListener("pointerleave", function () { tip.hidden = true; });

    var tbl = ui.el("div", "table-wrap"), tb = document.createElement("table"); tb.className = "table";
    var cap = ui.el("caption", null, "Voice time by hour of day (" + (tzOffset === null ? "server time" : offsetText(tzOffset)) + ")"); tb.appendChild(cap);
    var head = document.createElement("thead"), hr = document.createElement("tr");
    ["Hour", "Voice time"].forEach(function (t) { var th = ui.el("th", null, t); th.scope = "col"; hr.appendChild(th); });
    head.appendChild(hr); tb.appendChild(head);
    var body = document.createElement("tbody");
    seconds.forEach(function (s, i) { var tr = document.createElement("tr"); tr.appendChild(ui.el("td", null, hourLabel(i))); tr.appendChild(ui.el("td", null, hoursText(s))); body.appendChild(tr); });
    tb.appendChild(body); tbl.appendChild(tb); wrap.appendChild(tbl);
  }

  // ---------------------------------------------------------------- channels
  function drawChannels(channels, peaks) {
    var box = $("an-channels");
    box.textContent = "";
    if (!channels.length) { box.appendChild(ui.el("p", "muted", "No channel activity in this period.")); bind("topchan", "—"); bind("topchan-note", "No data"); return; }
    bind("topchan", channels[0].channel_name); bind("topchan-note", channels[0].formatted + " of voice time");
    var maxS = channels[0].total_seconds || 1;
    var ul = ui.el("ul", "hbars");
    channels.forEach(function (c) {
      var li = ui.el("li", "hbar-row");
      var head = ui.el("div", "hbar-head");
      head.appendChild(ui.el("span", "hbar-name", c.channel_name));
      head.appendChild(ui.el("span", "hbar-value", c.formatted));
      li.appendChild(head);
      var track = ui.el("div", "hbar-track"), fill = ui.el("div", "hbar-fill");
      fill.style.width = Math.max(1, Math.round((c.total_seconds / maxS) * 100)) + "%";       // CSSOM, allowed by the CSP
      track.appendChild(fill); li.appendChild(track);
      var p = peaks && peaks[c.channel_name];
      if (p) li.appendChild(ui.el("div", "small muted", "Most people at once: " + p));
      ul.appendChild(li);
    });
    box.appendChild(ul);
  }

  // ---------------------------------------------------------------- loading
  function load() {
    var mine = ++token;
    $("an-note").textContent = "Loading…";
    api.get("/api/analytics/dashboard?period=" + encodeURIComponent(period), { timeoutMs: 30000 }).then(function (d) {
      if (mine !== token) return;                               // a newer request replaced this one
      last = d;
      if (!d.enabled) {
        $("an-note").textContent = d.message || "Analytics are not available.";
        ["total", "sessions", "busiest", "topchan"].forEach(function (k) { bind(k, "—"); });
        $("an-chart").textContent = ""; $("an-channels").textContent = ""; return;
      }
      var total = d.hour_of_day_seconds.reduce(function (a, b) { return a + b; }, 0);
      bind("total", hoursText(total)); bind("sessions", String(d.total_sessions_analyzed));
      bind("tz", tzOffset === null ? "Server time" : "Server time, " + offsetText(tzOffset));
      drawHours(d.hour_of_day_seconds);
      drawChannels(d.channel_totals || [], d.peak_concurrent || {});
      $("an-note").textContent = "Updated " + new Date().toLocaleTimeString() + ".";
    }, function (err) {
      if (mine !== token) return;
      var msg = "Could not load analytics. " + ui.describeError(err);
      $("an-note").textContent = last ? msg + " Showing the previous data." : msg;
      if (!last) { ui.notice($("an-chart"), "error", "Analytics could not be loaded", [ui.describeError(err)]); $("an-channels").textContent = ""; }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll("#an-periods .tab-btn"), function (b) {
    b.addEventListener("click", function () {
      period = b.getAttribute("data-period");
      Array.prototype.forEach.call(document.querySelectorAll("#an-periods .tab-btn"), function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      load();
    });
  });
  $("an-table-btn").addEventListener("click", function () {
    var open = $("an-table-wrap").hidden;
    $("an-table-wrap").hidden = !open;
    this.setAttribute("aria-expanded", open ? "true" : "false");
    this.textContent = open ? "Hide table" : "View as table";
  });

  api.get("/api/status").then(function (s) { tzOffset = typeof s.timezone_offset_hours === "number" ? s.timezone_offset_hours : null; }, function () {}).then(load);
})();
