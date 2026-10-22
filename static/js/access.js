/* Access state for the new UI: does this server need a password, and has this
 * tab unlocked it?
 *
 * The server is the only authority. This script never decides who may act; it
 * only decides whether to SHOW controls as usable. A wrong or missing password
 * still gets a 401 from the server on every privileged request.
 *
 * States (VC.access.state()):
 *   unknown   status not read yet
 *   open      the server has no panel password; controls are usable by anyone
 *   locked    a password is required and this tab has not unlocked
 *   checking  verifying a password with the server
 *   unlocked  a password was verified in this tab
 */
(function () {
  "use strict";

  var required = null;            // null = unknown
  var state = "unknown";
  var listeners = [];
  var btn = document.getElementById("access-btn");
  var dialog = document.getElementById("unlock-dialog");
  var form = document.getElementById("unlock-form");
  var input = document.getElementById("unlock-input");
  var errBox = document.getElementById("unlock-error");
  var cancel = document.getElementById("unlock-cancel");
  var submit = document.getElementById("unlock-submit");
  var verifyStarted = false;

  function emit() {
    render();
    listeners.slice().forEach(function (fn) { try { fn(state); } catch (e) { /* a listener must not break the others */ } });
  }
  function set(next) { if (next !== state) { state = next; emit(); } else { render(); } }

  function render() {
    if (!btn) return;
    if (state === "open" || state === "unknown") { btn.hidden = true; return; }
    btn.hidden = false;
    btn.textContent = state === "unlocked" ? "Lock panel" : (state === "checking" ? "Checking…" : "Unlock");
    btn.disabled = state === "checking";
    btn.setAttribute("aria-label", state === "unlocked" ? "Lock the panel: forget the password in this tab" : "Unlock controls with the panel password");
  }

  /* Ask the server whether the stored password works. GET /api/toggles needs the
   * password and changes nothing. */
  function verify() {
    set("checking");
    return window.VC.api.get("/api/toggles", { auth: true }).then(function () {
      set("unlocked");
      return true;
    }, function (err) {
      if (err && err.kind === "unauthorized") {
        window.VC.api.clearPassword();
        set("locked");
        return false;
      }
      throw err;
    });
  }

  function onStatus(d) {
    if (!d || !d.web) return;
    var needs = !!d.web.password_protected;
    if (needs === required && state !== "unknown") return;
    required = needs;
    if (!needs) { window.VC.api.clearPassword(); set("open"); return; }
    if (window.VC.api.hasPassword() && !verifyStarted) {
      verifyStarted = true;
      verify().catch(function () { set("locked"); });     // could not reach the server: stay locked, never assume
    } else if (!window.VC.api.hasPassword()) {
      set("locked");
    }
  }

  function showError(msg) {
    if (!errBox) return;
    errBox.textContent = msg || "";
    errBox.hidden = !msg;
    if (input) input.setAttribute("aria-invalid", msg ? "true" : "false");
  }

  function openDialog() {
    if (!dialog || typeof dialog.showModal !== "function") return;
    showError("");
    if (input) input.value = "";
    dialog.showModal();
    if (input) input.focus();
  }

  function lockHere() {
    window.VC.api.clearPassword();
    set("locked");
    if (window.VC.toast) window.VC.toast("Controls locked. The password was forgotten in this tab.", "info");
  }

  if (btn) btn.addEventListener("click", function () { if (state === "unlocked") lockHere(); else if (state === "locked") openDialog(); });
  if (cancel) cancel.addEventListener("click", function () { if (dialog && dialog.open) dialog.close(); });

  if (form) form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var pw = input ? input.value : "";
    if (!pw) { showError("Enter the panel password."); return; }
    showError("");
    if (submit) submit.disabled = true;
    window.VC.api.setPassword(pw);
    if (input) input.value = "";
    verify().then(function (ok) {
      if (ok) { if (dialog.open) dialog.close(); if (window.VC.toast) window.VC.toast("Controls unlocked for this tab.", "success"); }
      else { showError("That password is not correct."); if (input) input.focus(); }
    }, function (err) {
      window.VC.api.clearPassword();
      set("locked");
      showError((err && err.message) || "Could not check the password. Try again.");
    }).then(function () { if (submit) submit.disabled = false; });
  });

  document.addEventListener("vc:status", function (ev) { onStatus(ev.detail); });

  window.VC = window.VC || {};
  window.VC.access = {
    state: function () { return state; },
    /** true when privileged controls should be shown as usable */
    canAct: function () { return state === "open" || state === "unlocked"; },
    onChange: function (fn) { listeners.push(fn); },
    open: openDialog,
    /** called by a page when the server answered 401: the password is no longer valid */
    expire: function () { window.VC.api.clearPassword(); if (required) set("locked"); }
  };
  render();
})();
