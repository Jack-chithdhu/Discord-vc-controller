/* Administration: feature switches and the birthday reminder interval.
 * Existing endpoints only (all need the panel password):
 *   GET /api/toggles                      POST /api/toggles/<key> {enabled}
 *   GET /api/birthday-reminder-interval   POST /api/birthday-reminder-interval {value, unit}
 * A change is shown as done only after the server's answer. */
(function () {
  "use strict";
  var VC = window.VC, ui = VC.ui, api = VC.api;
  if (!document.getElementById("admin-root")) return;
  var $ = function (id) { return document.getElementById(id); };
  var busy = false;

  function result(kind, title, lines) { ui.notice($("admin-result"), kind, title, lines); }

  function label(key) { return key.replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }); }

  function setBusy(b) {
    busy = b;
    Array.prototype.forEach.call(document.querySelectorAll("#toggle-list input"), function (i) { i.disabled = b; });
    $("bday-save").disabled = b;
  }

  function send(path, body) {
    setBusy(true);
    return api.post(path, body, { auth: true }).then(function (d) { setBusy(false); return d; }, function (err) {
      setBusy(false);
      if (err && err.kind === "unauthorized") VC.access.expire();
      throw err;
    });
  }

  // ---------------------------------------------------------------- feature switches
  function renderToggles(list) {
    var ul = $("toggle-list");
    ul.textContent = "";
    list.forEach(function (t) {
      var li = ui.el("li", "switch-row");
      var text = ui.el("div", "grow");
      text.appendChild(ui.el("div", "cap-name", label(t.key)));
      text.appendChild(ui.el("div", "cap-note", t.description));
      li.appendChild(text);
      var lab = ui.el("label", "switch");
      var inp = document.createElement("input");
      inp.type = "checkbox"; inp.setAttribute("role", "switch"); inp.checked = !!t.enabled;
      inp.id = "sw-" + t.key; inp.disabled = busy;
      inp.setAttribute("aria-label", label(t.key));
      inp.addEventListener("change", function () { changeToggle(t, inp); });
      lab.appendChild(inp);
      lab.appendChild(ui.el("span", "switch-state", t.enabled ? "On" : "Off"));
      li.appendChild(lab);
      ul.appendChild(li);
    });
  }

  function loadToggles() {
    return api.get("/api/toggles", { auth: true }).then(function (d) { renderToggles(d.toggles || []); }, function (err) {
      if (err && err.kind === "unauthorized") VC.access.expire();
      else { $("toggle-list").textContent = ""; $("toggle-list").appendChild(ui.el("li", "state state--error", "Could not load the switches. " + ui.describeError(err))); }
    });
  }

  function changeToggle(t, inp) {
    var want = inp.checked;
    inp.checked = !want;                       // the switch only moves when the server confirms
    ui.confirm((want ? "Turn on " : "Turn off ") + label(t.key) + "?", t.description, want ? "Turn on" : "Turn off", !want).then(function (ok) {
      if (!ok) return;
      send("/api/toggles/" + encodeURIComponent(t.key), { enabled: want }).then(function (d) {
        result("success", label(d.key) + " is now " + (d.enabled ? "on" : "off"), []);
        loadToggles();
      }, function (err) { result("error", "Could not change " + label(t.key), [ui.describeError(err)]); loadToggles(); });
    });
  }

  // ---------------------------------------------------------------- birthday interval
  function loadBirthday() {
    return api.get("/api/birthday-reminder-interval", { auth: true }).then(function (d) {
      $("bday-value").value = d.value; $("bday-unit").value = d.unit;
    }, function (err) {
      if (err && err.kind === "unauthorized") VC.access.expire();
      else showBdayError("Could not load the current interval. " + ui.describeError(err));
    });
  }
  function showBdayError(msg) { var e = $("bday-error"); e.textContent = msg || ""; e.hidden = !msg; $("bday-value").setAttribute("aria-invalid", msg ? "true" : "false"); }

  $("bday-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var raw = $("bday-value").value.trim(), value = Number(raw), unit = $("bday-unit").value;
    if (raw === "" || !isFinite(value) || value <= 0) { showBdayError("Enter a number greater than 0."); return; }
    showBdayError("");
    send("/api/birthday-reminder-interval", { value: value, unit: unit }).then(function (d) {
      result("success", "Birthday reminder interval saved", ["Every " + value + " " + unit + " (about " + Math.round(d.hours * 100) / 100 + " hours)."]);
      loadBirthday();
    }, function (err) { showBdayError(ui.describeError(err)); });
  });

  // ---------------------------------------------------------------- access
  $("admin-unlock").addEventListener("click", function () { VC.access.open(); });
  ui.onAccess({
    wait: $("admin-wait"), gate: $("admin-gate"), content: $("admin-content"),
    onUsable: function () { loadToggles(); loadBirthday(); }
  });
})();
