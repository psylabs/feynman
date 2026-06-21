// web/updates.js — confirm the freshly-applied OTA bundle booted OK, else the
// updater reverts to the previous bundle on next launch. Native-only.
(function () {
  if (!window.FEYNMAN_BUNDLED) return;
  var cap = window.Capacitor;
  var u = cap && cap.Plugins && cap.Plugins.CapacitorUpdater;
  if (u && typeof u.notifyAppReady === "function") u.notifyAppReady();
})();
