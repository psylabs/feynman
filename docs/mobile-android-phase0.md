# Phase 0 — Capacitor Android spike

Goal: prove that a Capacitor wrapper around the existing `web/` app, running natively on Android, can get reliable mic permission and round-trip a recording through Whisper. iOS is deferred. CORS work is deferred (we load the web app from your Tailscale URL, so the WebView is same-origin to the backend).

## What this scaffolds

- Root `package.json` with `@capacitor/core`, `@capacitor/cli`, `@capacitor/android`.
- `capacitor.config.json` with `webDir: "web"` and a placeholder `server.url`.
- `android/` — full Capacitor Android project.
- `RECORD_AUDIO` + `MODIFY_AUDIO_SETTINGS` + `<uses-feature ... microphone>` in `AndroidManifest.xml`.
- Capacitor's `BridgeActivity` already handles `WebChromeClient.onPermissionRequest` for `RESOURCE_AUDIO_CAPTURE`, so the existing `navigator.mediaDevices.getUserMedia` in `web/app.js` works unchanged inside the WebView.

No code in `web/` was modified. No CORS, no voice-recorder plugin, no SQLite, no notifications — those are later phases.

## Prerequisites (on the Mac mini)

1. **Android Studio** (Iguana or newer) with the Android SDK + a recent platform (API 34+).
2. **Node.js 20+** (Capacitor 8 requires it).
3. **An Android phone with USB debugging enabled**, or a willingness to transfer an APK via Google Drive / `adb install`.
4. **Tailscale HTTPS for the Mac mini backend.** The Android WebView blocks `getUserMedia` on plain HTTP. Run `tailscale cert` to mint a cert and serve the FastAPI app over HTTPS, or front it with Caddy / a reverse-proxy that terminates TLS. If you skip this, the mic will silently refuse to start.

## One-time setup

```sh
# From the repo root on the Mac mini
git pull
npm install
```

Edit `capacitor.config.json` and replace the placeholder:

```jsonc
"server": {
  "url": "https://YOUR-TAILSCALE-HOSTNAME.ts.net",   // <- your backend's HTTPS URL
  "cleartext": false,
  "androidScheme": "https"
}
```

Then sync the config into the Android project:

```sh
npx cap sync android
```

## Build & install on your phone

```sh
npx cap open android
```

In Android Studio:

1. Let Gradle sync (first run is slow — downloads the AGP + dependencies).
2. Plug in your phone, enable USB debugging when prompted.
3. Select your device in the device picker, click **Run** (▶).
4. The app installs and launches. The first time you press-and-hold the record button, Android will prompt for microphone permission — grant it.

If you'd rather sideload an APK without USB:

```sh
cd android
./gradlew assembleDebug
# outputs android/app/build/outputs/apk/debug/app-debug.apk — copy to phone & install
```

## Phase 0 acceptance checks

Run through these on the phone to confirm the spike passes:

- [ ] App launches; the existing start screen renders (loaded from your Tailscale URL).
- [ ] Tap a user, start a drill — questions appear, TTS audio plays back.
- [ ] First press-and-hold of "Press & hold to answer" triggers the native mic permission prompt. Grant it.
- [ ] Subsequent press-and-hold immediately starts recording — no permission prompt, no delay.
- [ ] On release, status shows "Transcribing…" and within ~1s the answer is graded — confirms upload + Whisper round-trip works.
- [ ] Lock the screen mid-session, unlock; the session resumes (WebView state preserved).
- [ ] Background the app for 5+ minutes, return. Confirm the WebView is intact or reloads cleanly.

## If the spike fails

- **Mic permission prompt never appears.** Check that `RECORD_AUDIO` is in the installed APK's manifest (it is in the repo). Check `chrome://inspect` from desktop Chrome — Capacitor exposes the WebView for remote debugging. Look for the JS error from `getUserMedia` in the console.
- **`getUserMedia` rejects with `SecurityError` / `NotSupportedError`.** Your `server.url` is not HTTPS. Fix Tailscale cert.
- **Recording works but transcription fails.** Check the FastAPI server logs (`/events` SSE stream still works) — likely a 4xx from `/session/submit` because the WebView's user-agent or content-type is rejected somewhere.
- **App loads but is blank.** `server.url` is unreachable from cellular. Confirm Tailscale is connected on the phone.

If any of the above are unfixable in a day, escalate to the plan: reconsider RN, or fall back to bundled `web/` + add CORS.

## What's *not* in Phase 0

- iOS (skipped per instruction).
- CORS middleware on the backend (skipped — we use `server.url` so the WebView is same-origin).
- `@capacitor-community/voice-recorder` plugin (deferred to Phase 2 alongside iOS).
- On-device SQLite, offline seed packs, local notifications, volume-button PTT.
- A configurable in-app Settings screen for the backend URL — for now, edit `capacitor.config.json` and `cap sync`.

These all land in later phases per `docs/migration-plan.md`.
