// web/config.js — MUST load before debug.js / users.js / app.js.
//
// In the bundled mobile app the UI is served locally (https://localhost on
// Android, capacitor://localhost on iOS) so it is a secure context and the
// microphone works. The API, however, lives on the Mac mini and is reached
// cross-origin over the Tailscale HTTPS hostname. In a plain browser (served by
// the backend itself) the app stays same-origin and nothing is rewritten.
(function () {
  "use strict";

  // Backend base URL used only when running as the bundled native app.
  // Override at runtime by setting window.FEYNMAN_API_BASE before this loads
  // (a settings screen will do this in a later phase).
  var DEFAULT_API_BASE = "https://pips-mac-mini.tail72bfb3.ts.net:8765";

  var cap = window.Capacitor;
  var isNative =
    (cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform()) ||
    location.protocol === "capacitor:" ||
    location.hostname === "localhost"; // bundled androidScheme=https serves from https://localhost

  var API_BASE = isNative ? (window.FEYNMAN_API_BASE || DEFAULT_API_BASE) : "";
  window.FEYNMAN_API_BASE = API_BASE;
  window.FEYNMAN_BUNDLED = isNative;

  // Prefix absolute-path API URLs ("/session/...") with the backend base.
  window.apiUrl = function (path) {
    if (typeof path === "string" && path.charAt(0) === "/") return API_BASE + path;
    return path;
  };

  if (API_BASE) {
    var _fetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      if (typeof input === "string" && input.charAt(0) === "/") input = API_BASE + input;
      return _fetch(input, init);
    };

    if (window.EventSource) {
      var _ES = window.EventSource;
      var Patched = function (url, cfg) {
        if (typeof url === "string" && url.charAt(0) === "/") url = API_BASE + url;
        return new _ES(url, cfg);
      };
      Patched.prototype = _ES.prototype;
      window.EventSource = Patched;
    }
  }

  // The service worker only makes sense when same-origin with the API (browser
  // / installed PWA). Inside the bundled app it would intercept paths that do
  // not exist on https://localhost, so register it only in the browser.
  if (!isNative && "serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
