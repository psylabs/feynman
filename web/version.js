// Version metadata source selection for browser vs bundled app.
(function () {
  "use strict";

  function usableVersion(info) {
    return !!(info && (info.sha || info.committed_at));
  }

  async function load(options) {
    options = options || {};
    var root = options.window || window;
    var fetchFn = options.fetch || root.fetch?.bind(root);
    var build = root.FEYNMAN_BUILD;
    if (root.FEYNMAN_BUNDLED && usableVersion(build)) {
      return Object.assign({ source: "app" }, build);
    }
    if (!fetchFn) throw new Error("fetch unavailable");
    var res = await fetchFn("/version", { cache: "no-store" });
    if (!res.ok) throw new Error("version HTTP " + res.status);
    return Object.assign({ source: "backend" }, await res.json());
  }

  window.feynmanVersion = { load: load };
})();
