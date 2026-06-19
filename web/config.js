// web/config.js — MUST load before debug.js / users.js / app.js.
//
// In the bundled mobile app the UI is served locally (https://localhost on
// Android, capacitor://localhost on iOS) so it is a secure context and the
// microphone works. The API, however, lives on the Mac mini and is reached
// cross-origin over the Tailscale HTTPS hostname. In a plain browser (served by
// the backend itself) the app stays same-origin and nothing is rewritten.
//
// The backend URL is configurable via the in-app Settings screen and persisted
// in localStorage (synchronous, so it is available before the first fetch — a
// Capacitor Preferences read would be async and race app startup).
(function () {
  "use strict";

  // Fallback when nothing is stored yet.
  var DEFAULT_API_BASE = "https://pips-mac-mini.tail72bfb3.ts.net:8765";
  var STORAGE_KEY = "feynman_api_base";

  var cap = window.Capacitor;
  var isNative =
    (cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform()) ||
    location.protocol === "capacitor:" ||
    location.hostname === "localhost"; // bundled androidScheme=https serves from https://localhost

  function storedBase() {
    try { return localStorage.getItem(STORAGE_KEY) || ""; } catch (e) { return ""; }
  }

  // currentBase is always meaningful (used by the Settings UI), but it is only
  // applied to requests when running as the bundled app.
  var currentBase = storedBase() || DEFAULT_API_BASE;

  window.FEYNMAN_BUNDLED = isNative;

  // Prefix absolute-path API URLs ("/session/...") with the backend base when
  // bundled; leave them same-origin in the browser.
  window.apiUrl = function (path) {
    if (isNative && typeof path === "string" && path.charAt(0) === "/") return currentBase + path;
    return path;
  };

  if (isNative) {
    var _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      if (typeof input === "string" && input.charAt(0) === "/") input = currentBase + input;
      return _fetch(input, init);
    };

    if (window.EventSource) {
      var _ES = window.EventSource;
      var Patched = function (url, cfg) {
        if (typeof url === "string" && url.charAt(0) === "/") url = currentBase + url;
        return new _ES(url, cfg);
      };
      Patched.prototype = _ES.prototype;
      window.EventSource = Patched;
    }
  }

  window.feynmanSettings = {
    isBundled: isNative,
    getApiBase: function () { return currentBase; },
    getDefaultApiBase: function () { return DEFAULT_API_BASE; },
    setApiBase: function (url) {
      url = (url || "").trim().replace(/\/+$/, "");
      currentBase = url || DEFAULT_API_BASE;
      try {
        if (url) localStorage.setItem(STORAGE_KEY, url);
        else localStorage.removeItem(STORAGE_KEY);
      } catch (e) {}
      return currentBase;
    },
    // Reachability check against /version for the given (or current) base.
    // Uses an absolute URL so the fetch shim leaves it untouched.
    test: function (url) {
      var base = (url != null ? url : currentBase).trim().replace(/\/+$/, "");
      return fetch(base + "/version", { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      });
    },
  };

  // The service worker only makes sense when same-origin with the API (browser
  // / installed PWA). Inside the bundled app it would intercept paths that do
  // not exist on https://localhost, so register it only in the browser.
  if (!isNative && "serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
