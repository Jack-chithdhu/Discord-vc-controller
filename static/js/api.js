/* Central fetch helper for the new UI. No framework.
 *
 * - Same-origin only. Never logs the password or puts it in a URL.
 * - The password, once the person unlocks controls, is kept in sessionStorage:
 *   that lives only in this browser tab, is cleared when the tab closes, and is
 *   never sent anywhere except the X-PW header of requests to this site.
 *   If storage is blocked the password simply lives in memory for this page.
 * - Every failure becomes an ApiError with a `kind` the UI can act on:
 *     offline | timeout | network | unauthorized | http | parse
 */
(function () {
  "use strict";

  var KEY = "vc.panel.pw";
  var password = "";
  try { password = window.sessionStorage.getItem(KEY) || ""; } catch (e) { password = ""; }

  function ApiError(kind, message, status, body) {
    this.name = "ApiError";
    this.kind = kind;
    this.message = message;
    this.status = status || 0;
    this.body = body || null;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function request(path, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      return Promise.reject(new ApiError("offline", "You are offline."));
    }
    var headers = { Accept: "application/json" };
    if (opts.auth && password) headers["X-PW"] = password;
    var init = { method: method, headers: headers, credentials: "same-origin", cache: "no-store" };
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    var ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timedOut = false;
    var timer = null;
    if (ctl) {
      init.signal = ctl.signal;
      timer = setTimeout(function () { timedOut = true; ctl.abort(); }, opts.timeoutMs || 10000);
    }
    return fetch(path, init).then(function (res) {
      if (timer) clearTimeout(timer);
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); }
          catch (e) { throw new ApiError("parse", "The server sent an unreadable response.", res.status); }
        }
        if (res.status === 401) throw new ApiError("unauthorized", "Password required or incorrect.", 401, data);
        if (!res.ok) {
          var msg = data && data.error ? String(data.error) : "Request failed (" + res.status + ").";
          throw new ApiError("http", msg, res.status, data);
        }
        return data;
      });
    }, function (err) {
      if (timer) clearTimeout(timer);
      if (err instanceof ApiError) throw err;
      if (timedOut) throw new ApiError("timeout", "The server took too long to answer.");
      throw new ApiError("network", "Could not reach the server.");
    });
  }

  window.VC = window.VC || {};
  window.VC.ApiError = ApiError;
  window.VC.api = {
    get: function (path, opts) { return request(path, opts); },
    post: function (path, body, opts) {
      opts = opts || {};
      opts.method = "POST";
      opts.body = body === undefined ? {} : body;
      return request(path, opts);
    },
    setPassword: function (pw) {
      password = pw ? String(pw) : "";
      try {
        if (password) window.sessionStorage.setItem(KEY, password);
        else window.sessionStorage.removeItem(KEY);
      } catch (e) { /* storage blocked: keep it in memory only */ }
    },
    clearPassword: function () { this.setPassword(""); },
    hasPassword: function () { return !!password; }
  };
})();
