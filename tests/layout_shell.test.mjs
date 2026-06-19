import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("top bar reserves OS status-bar safe area in CSS", () => {
  const css = readFileSync("web/styles.css", "utf8");

  assert.match(css, /--safe-area-top:\s*env\(safe-area-inset-top,\s*0px\)/);
  assert.match(css, /grid-template-rows:\s*calc\(var\(--topbar-height\)\s*\+\s*var\(--safe-area-top\)\)\s+1fr/);
  assert.match(css, /#topbar\s*\{[^}]*padding:\s*var\(--safe-area-top\)\s+16px\s+0/s);
});

test("Android activity applies system-bar insets to the WebView container", () => {
  const activity = readFileSync("android/app/src/main/java/com/psylabs/feynman/MainActivity.java", "utf8");

  assert.match(activity, /WindowCompat\.setDecorFitsSystemWindows\(getWindow\(\),\s*false\)/);
  assert.match(activity, /WindowInsetsCompat\.Type\.systemBars\(\)/);
  assert.match(activity, /setPadding\(bars\.left,\s*bars\.top,\s*bars\.right,\s*bars\.bottom\)/);
});
