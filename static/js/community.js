/* Community page: voice-time leaderboard, points leaderboard, member directory.
 *   GET /api/leaderboard?period=...          (public, existing)
 *   GET /api/profiles                        (public, existing; loaded only when asked, it covers everyone)
 *   GET /api/points-leaderboard?limit=10     (needs the panel password, added in Phase 4)
 * Member names are shown as text only. Profile links go to the existing /profile/<id> page. */
(function () {
  "use strict";
  var VC = window.VC, ui = VC.ui, api = VC.api;
  if (!document.getElementById("cm-root")) return;
  var $ = function (id) { return document.getElementById(id); };
  var period = "all";
  var PAGE = 50;

  function row(rank, id, name, value, linkId) {
    var li = ui.el("li", "rank-row");
    li.appendChild(ui.el("span", "rank", String(rank)));
    li.appendChild(ui.avatar(name, id));
    var who;
    if (linkId) { who = ui.el("a", "rank-name", name); who.href = "/profile/" + encodeURIComponent(linkId); }
    else who = ui.el("span", "rank-name", name);
    li.appendChild(who);
    li.appendChild(ui.el("span", "rank-value", value));
    return li;
  }

  function fail(box, title, err) {
    box.textContent = "";
    var s = ui.el("div", "state state--error"); s.setAttribute("role", "alert");
    s.appendChild(ui.el("span", "state-title", title));
    s.appendChild(ui.el("span", null, ui.describeError(err)));
    box.appendChild(s);
  }

  // ---------------------------------------------------------------- voice time
  function loadVc() {
    var box = $("cm-vc"), hi = $("cm-highlights");
    api.get("/api/leaderboard?period=" + encodeURIComponent(period)).then(function (d) {
      box.textContent = ""; hi.textContent = "";
      if (!d.enabled) { box.appendChild(ui.el("p", "muted", d.message || "Leaderboards are not available.")); return; }
      if (!d.vc_time.length) { box.appendChild(ui.el("p", "muted", "No voice time recorded for this period.")); return; }
      var ul = ui.el("ul", "log");
      d.vc_time.forEach(function (r, i) { ul.appendChild(row(i + 1, r.user_id, r.user_name || "Unknown", r.formatted, r.user_id)); });
      box.appendChild(ul);
      var lines = [];
      if (d.longest_session && d.longest_session.length) { var l = d.longest_session[0]; lines.push("Longest single session: " + (l.user_name || "Unknown") + ", " + l.formatted + (l.channel_name ? " in " + l.channel_name : "")); }
      if (d.night_owl) lines.push("Night owl (10pm to 4am): " + (d.night_owl.user_name || "Unknown") + ", " + d.night_owl.formatted);
      if (d.early_bird) lines.push("Early bird (5am to 9am): " + (d.early_bird.user_name || "Unknown") + ", " + d.early_bird.formatted);
      lines.forEach(function (t) { hi.appendChild(ui.el("p", "small text-2", t)); });
    }, function (err) { fail(box, "The leaderboard could not be loaded", err); hi.textContent = ""; });
  }

  Array.prototype.forEach.call(document.querySelectorAll("#cm-periods .tab-btn"), function (b) {
    b.addEventListener("click", function () {
      period = b.getAttribute("data-period");
      Array.prototype.forEach.call(document.querySelectorAll("#cm-periods .tab-btn"), function (o) { o.setAttribute("aria-pressed", o === b ? "true" : "false"); });
      $("cm-vc").textContent = ""; $("cm-vc").appendChild(ui.el("span", "skeleton"));
      loadVc();
    });
  });

  // ---------------------------------------------------------------- points (password)
  function loadPoints() {
    var box = $("cm-points");
    box.textContent = "";
    box.appendChild(ui.el("span", "skeleton"));
    api.get("/api/points-leaderboard?limit=10", { auth: true }).then(function (d) {
      box.textContent = "";
      if (d.feature_enabled === false) box.appendChild(ui.el("p", "small muted", "The points system is switched off in the bot. Balances below are what is stored."));
      if (!d.entries.length) { box.appendChild(ui.el("p", "muted", "No one has any points yet.")); return; }
      var ul = ui.el("ul", "log");
      d.entries.forEach(function (e) {
        var name = e.name || ("Member …" + String(e.user_id).slice(-4));
        ul.appendChild(row(e.rank, e.user_id, name, e.balance.toLocaleString() + " points", null));
      });
      box.appendChild(ul);
    }, function (err) {
      if (err && err.kind === "unauthorized") { VC.access.expire(); return; }
      fail(box, "Points could not be loaded", err);
    });
  }
  $("cm-points-unlock").addEventListener("click", function () { VC.access.open(); });
  ui.onAccess({ wait: $("cm-points-wait"), gate: $("cm-points-gate"), content: $("cm-points"), onUsable: loadPoints });

  // ---------------------------------------------------------------- member directory
  var people = [], shown = PAGE;
  function filtered() {
    var q = $("cm-search").value.trim().toLowerCase();
    return q ? people.filter(function (p) { return String(p.display_name || "").toLowerCase().indexOf(q) !== -1; }) : people;
  }
  function renderMembers() {
    var list = filtered(), ul = $("cm-members");
    ul.textContent = "";
    list.slice(0, shown).forEach(function (p, i) { ul.appendChild(row(i + 1, p.user_id, p.display_name || "Unknown", p.formatted + " in voice", p.user_id)); });
    $("cm-members-count").textContent = list.length ? "Showing " + Math.min(shown, list.length) + " of " + list.length + "." : "No members match.";
    $("cm-more").hidden = shown >= list.length;
  }
  $("cm-load-members").addEventListener("click", function () {
    var btn = $("cm-load-members"), msg = $("cm-members-msg");
    btn.disabled = true; msg.textContent = "Loading members…";
    api.get("/api/profiles", { timeoutMs: 30000 }).then(function (d) {
      people = d.people || []; shown = PAGE; msg.textContent = "";
      $("cm-members-box").hidden = false; btn.textContent = "Reload members"; btn.disabled = false;
      renderMembers();
    }, function (err) { btn.disabled = false; ui.notice(msg, "error", "Members could not be loaded", [ui.describeError(err)]); });
  });
  $("cm-search").addEventListener("input", function () { shown = PAGE; renderMembers(); });
  $("cm-more").addEventListener("click", function () { shown += PAGE; renderMembers(); });

  loadVc();
})();
