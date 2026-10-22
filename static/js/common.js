/* Small shared helpers for page scripts. Everything builds DOM with
 * textContent, so server text (member and channel names) can never inject markup. */
(function () {
  "use strict";
  var VC = (window.VC = window.VC || {});

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function fmtDuration(seconds) {
    var s = Math.max(0, Math.round(Number(seconds) || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return h + "h " + m + "m";
    return m + "m";
  }

  function timeAgo(iso) {
    if (!iso) return "";
    var t = Date.parse(iso);
    if (isNaN(t)) return "";
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return "just now";
    var m = Math.floor(s / 60);
    if (m < 60) return m + " min ago";
    var h = Math.floor(m / 60);
    if (h < 24) return h + " h ago";
    return Math.floor(h / 24) + " d ago";
  }

  /** One short sentence for any failure the API helper can raise. */
  function describeError(err) {
    if (!err) return "Something went wrong.";
    if (err.kind === "unauthorized") return "The password is missing or no longer valid. Unlock controls and try again.";
    if (err.kind === "offline") return "You are offline.";
    if (err.kind === "timeout") return "The server took too long to answer.";
    if (err.kind === "network") return "Could not reach the server.";
    return err.message || "Something went wrong.";
  }

  var AV = 5;
  function avatar(name, id) {
    var chars = Array.from(String(name || "?").trim());
    var n = el("span", "avatar av-" + (Math.abs(parseInt(String(id).slice(-4), 10) || 0) % AV), (chars[0] || "?").toUpperCase());
    n.setAttribute("aria-hidden", "true");
    return n;
  }

  var ACTIONS = {
    JOINED: ["Joined", function (e) { return e.to || ""; }],
    LEFT: ["Left", function (e) { return e.from || ""; }],
    DISCONNECTED: ["Disconnected", function (e) { return "from " + (e.from || ""); }],
    MOVED: ["Moved", function (e) { return (e.from || "") + " → " + (e.to || ""); }],
    LOCK_VIOLATION: ["Lock violation", function (e) { return "tried " + (e.to || "a locked channel"); }],
    LOCK_ENABLED: ["Locked", function (e) { return e.details || ""; }],
    LOCK_DISABLED: ["Unlocked", function (e) { return e.details || ""; }]
  };

  /** A row for one /api/voice_log event. */
  function logRow(e) {
    var action = String(e.action || e.event || "").toUpperCase();
    var known = ACTIONS[action];
    var row = el("li", "log-row");
    var main = el("div", "log-main");
    if (e.name) main.appendChild(el("strong", null, e.name));
    main.appendChild(el("span", "log-tag", known ? known[0] : action || "Event"));
    var detail = known ? known[1](e) : (e.details || "");
    if (detail) main.appendChild(el("span", "text-2", detail));
    if (e.actor_name) main.appendChild(el("span", "muted", "by " + e.actor_name));
    row.appendChild(main);
    row.appendChild(el("span", "log-time muted", timeAgo(e.time)));
    return row;
  }

  VC.ui = { el: el, fmtDuration: fmtDuration, timeAgo: timeAgo, describeError: describeError, avatar: avatar, logRow: logRow };
})();
