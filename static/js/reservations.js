/* Reservations: the booking-limits editor.
 *   GET  /api/reservation-settings        (password) the current limits
 *   POST /api/reservation-settings {...}  (password) changes only the fields sent
 *   GET  /api/status                      to warn when the server timezone is not set
 *
 * The server saves fields one by one, so a request with a bad field can leave an
 * earlier field saved. This page therefore (1) sends only the fields you changed,
 * (2) checks them first, and (3) re-reads the real values after every save. */
(function () {
  "use strict";
  var VC = window.VC, ui = VC.ui, api = VC.api;
  if (!document.getElementById("rv-root")) return;
  var $ = function (id) { return document.getElementById(id); };
  var FIELDS = ["cooldown_minutes", "expire_after_hours", "max_pending", "max_per_day", "max_active",
                "spam_protection_enabled", "duplicate_protection_enabled", "auto_expire_enabled"];
  var saved = null;      // the values the server last returned
  var busy = false;

  function input(f) { return $("rv-" + f); }
  function current(f) { var i = input(f); return i.type === "checkbox" ? i.checked : i.value.trim(); }

  function fill(cfg) {
    saved = cfg;
    FIELDS.forEach(function (f) { var i = input(f); if (i.type === "checkbox") i.checked = !!cfg[f]; else i.value = cfg[f]; });
    refresh();
  }

  /** Fields whose value differs from what the server has, checked and converted. */
  function changes() {
    var out = {}, problems = [];
    FIELDS.forEach(function (f) {
      var i = input(f), now = current(f);
      if (i.type === "checkbox") { if (now !== !!saved[f]) out[f] = now; return; }
      i.setAttribute("aria-invalid", "false");
      if (now === "") { problems.push(label(f) + " needs a number."); i.setAttribute("aria-invalid", "true"); return; }
      var n = Number(now);
      if (!isFinite(n) || n < 0) { problems.push(label(f) + " must be 0 or more."); i.setAttribute("aria-invalid", "true"); return; }
      if (i.getAttribute("data-kind") === "int" && Math.floor(n) !== n) { problems.push(label(f) + " must be a whole number."); i.setAttribute("aria-invalid", "true"); return; }
      if (n !== Number(saved[f])) out[f] = n;
    });
    return { values: out, problems: problems };
  }
  function label(f) { var l = document.querySelector('label[for="rv-' + f + '"]'); return l ? l.textContent : f; }

  function refresh() {
    if (!saved) return;
    var c = changes(), n = Object.keys(c.values).length;
    $("rv-error").hidden = true;
    $("rv-save").disabled = busy || n === 0 || c.problems.length > 0;
    $("rv-reset").disabled = busy || (n === 0 && c.problems.length === 0);
    $("rv-dirty").textContent = c.problems.length ? c.problems[0] : (n ? n + " unsaved change" + (n === 1 ? "" : "s") : "No changes");
  }

  function load() {
    return api.get("/api/reservation-settings", { auth: true }).then(fill, function (err) {
      if (err && err.kind === "unauthorized") VC.access.expire();
      else ui.notice($("rv-result"), "error", "Could not load the limits", [ui.describeError(err)]);
    });
  }

  $("rv-content").addEventListener("input", refresh);
  $("rv-content").addEventListener("change", refresh);
  $("rv-reset").addEventListener("click", function () { if (saved) fill(saved); ui.notice($("rv-result"), "info", "Changes discarded", []); });

  $("rv-content").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var c = changes();
    if (c.problems.length) { var e = $("rv-error"); e.textContent = c.problems.join(" "); e.hidden = false; return; }
    var keys = Object.keys(c.values);
    if (!keys.length) return;
    var lines = keys.map(function (k) { return label(k) + ": " + saved[k] + " → " + c.values[k]; });
    ui.confirm("Save " + keys.length + " change(s)?", lines.join(". "), "Save", false).then(function (ok) {
      if (!ok) return;
      busy = true; refresh();
      api.post("/api/reservation-settings", c.values, { auth: true }).then(function (cfg) {
        busy = false; fill(cfg);
        ui.notice($("rv-result"), "success", "Limits saved", lines);
      }, function (err) {
        busy = false;
        if (err && err.kind === "unauthorized") VC.access.expire();
        ui.notice($("rv-result"), "error", "Could not save", [ui.describeError(err), "Showing what the server actually has now."]);
        load();                                     // some fields may have been saved: show the truth
      });
    });
  });

  $("rv-unlock").addEventListener("click", function () { VC.access.open(); });
  ui.onAccess({ wait: $("rv-wait"), gate: $("rv-gate"), content: $("rv-content"), onUsable: load });

  api.get("/api/status").then(function (d) { $("rv-tz").hidden = d.timezone_configured !== false; }, function () { /* the status chip already says so */ });
})();
