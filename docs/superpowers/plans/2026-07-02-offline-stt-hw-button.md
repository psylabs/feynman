# Offline Voice Answers + Hardware PTT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer drills by holding volume-down and speaking, fully offline, with the screen kept awake — on the Capacitor Android app.

**Architecture:** A tiny custom Capacitor plugin (`PttKeys`) intercepts volume-down and holds a keep-screen-on flag; offline sessions get a voice path through `@capacitor-community/speech-recognition` (Android's on-device recognizer) whose transcript feeds the existing `parseTypedAnswer → gradeAnswer → SQLite sync` pipeline. Online behavior (server STT) is unchanged.

**Tech Stack:** Capacitor 8, vanilla JS in `web/` (no bundler; plugins via `window.Capacitor.Plugins.X` — match how `web/updates.js` uses `CapacitorUpdater`), Java in `android/`, node `.mjs` tests.

## Global Constraints

- Surgical edits only; follow existing file style (vanilla JS IIFE modules, no framework).
- Feature-detect every plugin (`window.Capacitor?.Plugins?.X`) — a new OTA bundle on an old APK must fail soft to current behavior.
- Spec: `docs/superpowers/specs/2026-07-02-offline-stt-hw-button-design.md`.
- Native verify: `npm run cap:sync && cd android && ./gradlew :app:assembleDebug`.
- Volume-up is never intercepted. No server changes expected (verify Task 3).

---

### Task 1: Words-to-numbers fallback in `parseTypedAnswer`

**Files:** Modify `web/offline.js:482-494` · Test (create): `tests/spoken_number.test.mjs`

**Interfaces:** Produces: `parseTypedAnswer(raw)` (already exported on `window` at `web/offline.js:678`) now also parses number words when no digits are present. Same return shape `{value, skipped, raw}`.

- [ ] **Step 1: Failing test.** Follow the import pattern of `tests/offline_store.test.mjs` to load `web/offline.js` and test `parseTypedAnswer`. Cases: `"42"→42`, `"forty two"→42`, `"forty-two"→42`, `"one hundred and five"→105`, `"one thousand two hundred thirty four"→1234`, `"seven point five"→7.5`, `"negative eight"→-8`, `"a hundred"→100`, `"skip"→skipped:true`, `"banana"→value:null`. Run `node --test tests/spoken_number.test.mjs` → FAIL on word cases.
- [ ] **Step 2: Implement** `wordsToNumber(text)` in `web/offline.js` next to `parseTypedAnswer`, and call it in `parseTypedAnswer` when the digit regex misses:

```js
var SMALL = { zero:0, one:1, two:2, three:3, four:4, five:5, six:6, seven:7,
  eight:8, nine:9, ten:10, eleven:11, twelve:12, thirteen:13, fourteen:14,
  fifteen:15, sixteen:16, seventeen:17, eighteen:18, nineteen:19, twenty:20,
  thirty:30, forty:40, fifty:50, sixty:60, seventy:70, eighty:80, ninety:90 };

function wordsToNumber(text) {
  var tokens = text.replace(/-/g, " ").split(/\s+/).filter(function (t) { return t && t !== "and"; });
  if (!tokens.length) return null;
  var neg = tokens[0] === "negative" || tokens[0] === "minus";
  if (neg) tokens.shift();
  var total = 0, current = 0, seen = false, i, t;
  for (i = 0; i < tokens.length; i++) {
    t = tokens[i];
    if (t === "point") break;
    if (t === "a") { if (!current) current = 1; }
    else if (t in SMALL) { current += SMALL[t]; seen = true; }
    else if (t === "hundred") { current = (current || 1) * 100; seen = true; }
    else if (t === "thousand") { total += (current || 1) * 1000; current = 0; seen = true; }
    else if (t === "million") { total += (current || 1) * 1000000; current = 0; seen = true; }
    else return null;
  }
  var value = total + current;
  if (i < tokens.length && tokens[i] === "point") {
    var frac = "";
    for (var j = i + 1; j < tokens.length; j++) {
      t = tokens[j];
      if (!(t in SMALL) || SMALL[t] > 9) return null;
      frac += SMALL[t];
    }
    if (!frac) return null;
    value = parseFloat(String(value) + "." + frac);
    seen = true;
  }
  if (!seen) return null;
  return neg ? -value : value;
}
```

In `parseTypedAnswer`, replace `if (!match) return { value: null, skipped: false, raw: raw };` with a `wordsToNumber(cleaned)` attempt first.
- [ ] **Step 3: Verify** `node --test tests/spoken_number.test.mjs` PASS, and `node --test tests/offline_store.test.mjs` still PASS. Commit `feat(offline): parse spoken number words in typed-answer parser`.

### Task 2: `PttKeys` native plugin (volume-down intercept + keep-awake)

**Files:** Create `android/app/src/main/java/com/psylabs/feynman/PttKeysPlugin.java` · Modify `android/app/src/main/java/com/psylabs/feynman/MainActivity.java`

**Interfaces:** Produces (to JS): plugin `PttKeys` with methods `setArmed({armed: boolean})`, `setKeepAwake({on: boolean})`; events `pttDown` / `pttUp` (empty payload). While armed, volume-down never reaches the system (no volume change); volume-up always passes through. Key repeats are swallowed, not re-emitted.

- [ ] **Step 1: Plugin.**

```java
package com.psylabs.feynman;

import android.view.KeyEvent;
import android.view.WindowManager;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "PttKeys")
public class PttKeysPlugin extends Plugin {
    private volatile boolean armed = false;

    @PluginMethod
    public void setArmed(PluginCall call) {
        armed = Boolean.TRUE.equals(call.getBoolean("armed", false));
        call.resolve();
    }

    @PluginMethod
    public void setKeepAwake(PluginCall call) {
        boolean on = Boolean.TRUE.equals(call.getBoolean("on", false));
        getActivity().runOnUiThread(() -> {
            if (on) getActivity().getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            else getActivity().getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        });
        call.resolve();
    }

    boolean handleKey(KeyEvent event) {
        if (!armed || event.getKeyCode() != KeyEvent.KEYCODE_VOLUME_DOWN) return false;
        if (event.getRepeatCount() > 0) return true;
        notifyListeners(event.getAction() == KeyEvent.ACTION_DOWN ? "pttDown" : "pttUp", new JSObject());
        return true;
    }
}
```

- [ ] **Step 2: MainActivity.** Add `registerPlugin(PttKeysPlugin.class);` as the first line of `onCreate` (before the window calls), and override:

```java
@Override
public boolean dispatchKeyEvent(KeyEvent event) {
    if (getBridge() != null) {
        var handle = getBridge().getPlugin("PttKeys");
        if (handle != null && ((PttKeysPlugin) handle.getInstance()).handleKey(event)) return true;
    }
    return super.dispatchKeyEvent(event);
}
```

(Adjust to the Capacitor 8 API if `getPlugin` differs — compile is the test.)
- [ ] **Step 3: Verify** `cd android && ./gradlew :app:compileDebugJavaWithJavac` succeeds. Commit `feat(android): PttKeys plugin — volume-down PTT + keep-awake`.

### Task 3: Wire PTT dispatcher, arming, and keep-awake in JS

**Files:** Modify `web/app.js` (input bindings `:1857-1911`, session lifecycle)

**Interfaces:** Consumes `PttKeys` from Task 2. Produces: `pttPress()` / `pttRelease()` dispatcher that Task 4 extends with the offline-voice branch; `setSessionActive(on)` called from every session start/end path.

- [ ] **Step 1: Dispatcher.** Add near the input bindings: `function pttPress() { startRecording(); }` and `function pttRelease() { stopRecording(); }` (Task 4 adds the offline branch). Rewire the `#btn-ptt` pointer handlers and the Space handlers to call them. **Delete** the dead `AudioVolumeUp`/`AudioVolumeDown` branches and the `volumeKeyDown` flag (`web/app.js:1887, 1895-1898, 1906-1910`) — WebView never fires them.
- [ ] **Step 2: PttKeys listeners + session arming.**

```js
const PttKeys = window.Capacitor?.Plugins?.PttKeys || null;
if (PttKeys) {
  PttKeys.addListener("pttDown", () => { if (!$("btn-ptt").disabled) pttPress(); });
  PttKeys.addListener("pttUp", () => pttRelease());
}
function setSessionActive(on) {
  PttKeys?.setArmed({ armed: !!on });
  PttKeys?.setKeepAwake({ on: !!on });
}
```

Call `setSessionActive(true)` at the start of every session path — `startSession(...)`, the offline start (`web/app.js:~539-555`), and the offline continue (`web/app.js:~654-674`) — and `setSessionActive(false)` in `endSession()` (find it; it's the shared exit). Guard against double-calls being harmless (they are: idempotent flags).
- [ ] **Step 3: Verify + commit.** `node --test tests/*.test.mjs` still passes (no DOM regressions pulled in). Manual check deferred to Task 5. Commit `feat(web): route PTT through dispatcher; arm volume-down + keep-awake per session`.

### Task 4: Offline voice answers via on-device recognizer

**Files:** Modify `package.json` (dependency), `web/app.js` (offline question flow `:688-742`, dispatcher from Task 3), `web/offline.js` (`buildOfflineAttempt` `:496-512`)

**Interfaces:** Consumes `pttPress`/`pttRelease` (Task 3), `parseTypedAnswer` (Task 1). Produces: offline attempts with `answer_mode: "voice_offline"` when answered by voice.

- [ ] **Step 1: Install plugin.** `npm info @capacitor-community/speech-recognition versions` — install the major matching Capacitor 8 (`npm i @capacitor-community/speech-recognition@^8` if it exists, else latest and verify the Gradle build in Step 4 passes; if it cannot compile against Cap 8, STOP and report rather than forking). `npm run cap:sync`.
- [ ] **Step 2: Offline voice path in `web/app.js`.**

```js
const Speech = window.Capacitor?.Plugins?.SpeechRecognition || null;
let nativeSttReady = false;
(async () => { try { nativeSttReady = Speech && (await Speech.available()).available === true; } catch {} })();

let sttPartial = "", sttFinal = null, sttDone = Promise.resolve();
function offlineVoicePress() {
  sttPartial = ""; sttFinal = null;
  $("btn-ptt").classList.add("recording"); $("btn-ptt").textContent = "Recording…";
  Speech.removeAllListeners();
  Speech.addListener("partialResults", (d) => { if (d?.matches?.length) sttPartial = d.matches[0]; });
  sttDone = Speech.start({ language: "en-US", partialResults: true, popup: false })
    .then((r) => { sttFinal = r?.matches?.[0] ?? null; }).catch(() => {});
}
async function offlineVoiceRelease() {
  $("btn-ptt").classList.remove("recording"); $("btn-ptt").textContent = "Press & hold to answer";
  try { await Speech.stop(); } catch {}
  await Promise.race([sttDone, new Promise((r) => setTimeout(r, 2000))]);
  const transcript = (sttFinal || sttPartial || "").trim();
  if (!transcript) { $("status").textContent = "Didn't catch that — try again or type."; return; }
  submitTypedAnswer(transcript, "voice_offline");
}
```

Branch the dispatcher: in `pttPress`/`pttRelease`, if `offlineSession && nativeSttReady` use the offline pair. In `nextOfflineQuestion` (`web/app.js:694-697`), when `nativeSttReady`, un-hide/enable `#btn-ptt` at the same moment `showTypedAnswer(false)` runs (the `enable` callback), so both voice and typing work. Do NOT call `ensureMicStream`/`MediaRecorder` on this path (native recognizer owns the mic; `releaseMicStream("offline_start")` at `:549` already ran).
- [ ] **Step 3: `answer_mode` plumbing.** Thread a mode through: `submitTypedAnswer(raw, mode)` → offline branch → `buildOfflineAttempt(seed, rawAnswer, timing, mode)` → `answer_mode: mode || "typed"` (`web/offline.js:511`). Check `server/` bulk-attempt ingestion (see `tests/test_bulk_attempts.py`) treats `answer_mode` as a pass-through string — if it's enum-validated, add `"voice_offline"`; otherwise no server change. Extend `tests/spoken_number.test.mjs` or `tests/offline_store.test.mjs` with one case asserting `buildOfflineAttempt(seed, "forty two", {}, "voice_offline")` yields `parsed_answer: 42, answer_mode: "voice_offline"`.
- [ ] **Step 4: Verify** `node --test tests/*.test.mjs` and `cd android && ./gradlew :app:assembleDebug` pass. Commit `feat(offline): voice answers via on-device speech recognition`.

### Task 5: Build, install, hand off for device test

- [ ] **Step 1:** `npm run cap:sync && cd android && ./gradlew :app:assembleDebug`, then `adb install -r android/app/build/outputs/apk/debug/app-debug.apk` (if a device is attached; otherwise report the APK path).
- [ ] **Step 2:** Report the manual test checklist to the user (do not claim success without device results): volume-down PTT online; airplane-mode drill answered by voice (needs offline language pack: Settings → Google → voice typing); graded locally + syncs on reconnect; screen stays on through a session and sleeps normally after; volume keys behave normally outside sessions. Note: `web/` changes also ship via `npm run publish` OTA, but only onto this new APK.
