import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadVersionModule({ bundled = false, buildInfo = null, fetchImpl = null } = {}) {
  const calls = [];
  const context = {
    console,
    window: {
      FEYNMAN_BUNDLED: bundled,
      FEYNMAN_BUILD: buildInfo,
      fetch: fetchImpl || (async (url, options) => {
        calls.push({ url, options });
        return {
          ok: true,
          json: async () => ({
            sha: "server1",
            message: "Backend commit",
            committed_at: "2026-06-19T10:00:00Z",
          }),
        };
      }),
    },
  };
  vm.runInNewContext(readFileSync("web/version.js", "utf8"), context);
  return { api: context.window.feynmanVersion, calls };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test("bundled app version prefers embedded build metadata without fetching backend", async () => {
  const { api, calls } = loadVersionModule({
    bundled: true,
    buildInfo: {
      sha: "abc1234",
      message: "Latest APK",
      committed_at: "2026-06-19T11:00:00Z",
    },
  });

  const version = await api.load();

  assert.equal(version.source, "app");
  assert.equal(version.sha, "abc1234");
  assert.equal(version.message, "Latest APK");
  assert.equal(calls.length, 0);
});

test("browser version falls back to backend /version", async () => {
  const { api, calls } = loadVersionModule();

  const version = await api.load();

  assert.equal(version.source, "backend");
  assert.equal(version.sha, "server1");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/version");
  assert.deepEqual(plain(calls[0].options), { cache: "no-store" });
});

test("bundled app falls back to backend if build metadata is absent", async () => {
  const { api, calls } = loadVersionModule({ bundled: true });

  const version = await api.load();

  assert.equal(version.source, "backend");
  assert.equal(version.sha, "server1");
  assert.equal(calls.length, 1);
});
