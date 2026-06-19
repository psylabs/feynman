import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function git(args) {
  return execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
}

const fullSha = process.env.GITHUB_SHA || git(["rev-parse", "HEAD"]);
const info = {
  sha: fullSha.slice(0, 7),
  full_sha: fullSha,
  message: git(["log", "-1", "--format=%s"]),
  committed_at: git(["log", "-1", "--format=%cI"]),
  source: "app",
};

writeFileSync(
  resolve(root, "web/build-info.js"),
  `window.FEYNMAN_BUILD = ${JSON.stringify(info, null, 2)};\n`,
);
