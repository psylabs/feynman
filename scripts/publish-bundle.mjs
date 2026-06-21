// scripts/publish-bundle.mjs — zip web/ and bump the OTA manifest. Local-only.
import { execFileSync } from "node:child_process";
import { writeFileSync, mkdirSync, renameSync, readdirSync, statSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const run = (cmd, args) =>
  execFileSync(cmd, args, { cwd: root, encoding: "utf8" }).trim();

// 1. Divergence guard: a dirty tree means the zip wouldn't match any commit.
if (run("git", ["status", "--porcelain", "--", "web"])) {
  console.error("Working tree is dirty — commit before publishing so the OTA bundle == a real commit.");
  process.exit(1);
}
const sha = run("git", ["rev-parse", "--short", "HEAD"]);

// 2. Stamp build-info.js from HEAD so the in-app version readout tracks the OTA bundle.
run("node", ["scripts/write-build-info.mjs"]);
try {
  // 3. Zip web/ with the Capgo CLI (produces zip + sha256 checksum).
  //    Contract confirmed in Task 1: --json prints {bundle, filename, checksum};
  //    `filename` is a BASENAME written into cwd (= root here); checksum is hex sha256.
  //    --no-code-check skips Capgo's JS bundler heuristics (we ship raw web/).
  const out = run("npx", ["@capgo/cli", "bundle", "zip", "--path", "web", "--no-code-check", "--json"]);
  const { filename, checksum } = JSON.parse(out);

  // 4. Move zip into the served dir and write the manifest pointer.
  const bundlesDir = resolve(root, "data/bundles");
  mkdirSync(bundlesDir, { recursive: true });
  renameSync(resolve(root, filename), resolve(bundlesDir, `${sha}.zip`));
  writeFileSync(
    resolve(bundlesDir, "latest.json"),
    JSON.stringify({ version: sha, checksum, file: `${sha}.zip` }, null, 2) + "\n",
  );

  // 5. Prune old bundles — only latest.json is ever served. Keep the few most
  //    recent so a bad release can be rolled back by repointing latest.json.
  const KEEP = 3;
  readdirSync(bundlesDir)
    .filter((f) => f.endsWith(".zip"))
    .map((f) => ({ f, t: statSync(resolve(bundlesDir, f)).mtimeMs }))
    .sort((a, b) => b.t - a.t)
    .slice(KEEP)
    .forEach(({ f }) => rmSync(resolve(bundlesDir, f)));
} finally {
  // 6. Restore build-info.js to its committed (null) state — keep the tree clean.
  run("git", ["checkout", "--", "web/build-info.js"]);
}
console.log(`Published ${sha}`);
