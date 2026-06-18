# Feynman → Mobile Migration Plan

## Context

Moving Feynman from a Mac-mini-hosted web app (accessed via Tailscale on phone) to an installed mobile app. The drivers, in priority order:

1. **Offline mode on the train.** Today the app is dead without LAN/Tailscale.
2. **Reliable iOS mic.** iOS Safari/Chrome `getUserMedia` has been the blocker; native permission fixes it.
3. **Local + push notifications** to nudge daily use ("get used" is the stated goal — "everything else is theatre").
4. **Volume-button PTT** as a nice-to-have.
5. **Server reliability** — the Mac mini server keeps needing manual restarts.

This is a personal app, not store-bound, but built so it could become valuable. AI must stay Python, server-side. The "managed agent" layer (memory, dynamic planning, feedback) is a separate workstream that doesn't constrain mobile choice.

## Codebase reality check (this drives the recommendation)

Findings from reading the actual code:

- **Frontend is vanilla JS + custom CSS**, ~1,280 LOC across `web/app.js`, `web/users.js`, `web/debug.js`, `web/styles.css`, `web/index.html`. No React/Vue/Svelte. No bundler, no package.json. 5 screens via `classList.toggle`. Auth: none (just a `localStorage` user-id picker).
- **PWA infra already there**: `web/manifest.json` (standalone, icons, theme) and `web/sw.js` (stale-while-revalidate for shell, network-only for API).
- **Audio pipeline**: `navigator.mediaDevices.getUserMedia` → `MediaRecorder` (webm) → multipart POST to `/session/submit` → server saves blob → `gpt-4o-mini-transcribe` (`server/stt.py`). No streaming.
- **Volume PTT already works on Android Chrome** via `AudioVolumeUp/Down` keydown events (`web/app.js:991-1006`). Spacebar hold too.
- **Backend**: FastAPI at `server/main.py` (port 8765), SQLite at `data/feynman.db`. SSE on `/events` for debug only. OpenAI TTS/STT/feedback. Generators are pure Python (`server/generator.py`, `money.py`, `weather.py`). No CORS middleware. Server raises FD limits at startup because macOS defaults crash it under SSE+static load.
- **No notifications, no offline data layer, no background jobs** (one `BackgroundTasks` call for async feedback gen).

The thing being "wrapped" is small, framework-free, and already PWA-ish. A rewrite would 3-5× the surface area for zero UX gain.

## Recommendation: **Capacitor**, with native plugins for mic, SQLite, notifications, and (Android-only) volume keys

Reasoning grounded in the code:

- **Reuse is near-total.** The `web/` directory is the WebView payload almost as-is. No framework port. No router rewrite. The existing service worker stays (it caches the shell correctly).
- **iOS mic gets fixed.** Capacitor mounts WKWebView on iOS 14.5+, which supports `getUserMedia` natively when `Info.plist` declares `NSMicrophoneUsageDescription`. For belt-and-suspenders, swap `MediaRecorder` for the `@capacitor-community/voice-recorder` plugin so audio capture goes through native AVAudioSession (m4a/aac, Whisper-compatible) — bypasses any remaining WebView audio quirks.
- **Notifications are a one-plugin install** (`@capacitor/local-notifications`). Local notifications cover the actual need ("nudge to use it daily"); FCM/APNs push only matters if the *server* needs to wake the phone, which is a future concern.
- **SQLite on-device** via `@capacitor-community/sqlite` mirrors the server schema. Copy the relevant DDL out of `schema.sql` more or less verbatim.
- **Android volume PTT is straightforward**: ~30-line MainActivity override emits keydown events into the WebView. The existing `app.js:991` handler then works unchanged.
- **iOS volume PTT is effectively impossible cleanly.** Apple does not permit hardware button interception. The only workable hack is observing `AVAudioSession.outputVolume`, which Apple sometimes rejects but works on a sideloaded personal build. Acceptable downgrade since it's a nice-to-have.
- **Backend stays put.** Add CORS, make the base URL configurable in the app's settings, run the existing FastAPI process under a launchd plist on the Mac mini so it auto-restarts (this fixes the "restart server" pain without touching the mobile work).

What you give up vs. RN/Flutter: nothing material. No native UI ambitions, no heavy on-device compute, no appetite for a rewrite.

## Decision matrix

Scored 1-5 against the requirements (5 = best). Capacitor is ahead because rewrite cost dominates a 1.3K-LOC vanilla JS app.

| Criterion | Capacitor | React Native / Expo | Flutter | PWA |
|---|---|---|---|---|
| Rewrite cost (5 = lowest) | **5** | 1 | 1 | **5** |
| "Lightweight / easy to dev" | 4 | 3 | 3 | **5** |
| iOS mic permission (reliable) | 4 (with voice-recorder plugin) | **5** | **5** | 2 (Safari quirks) |
| Android mic permission | **5** | **5** | **5** | 4 |
| Local notifications iOS | **5** | **5** | **5** | 2 (iOS 16.4+ only, installed PWA only) |
| Local notifications Android | **5** | **5** | **5** | 4 |
| Push notifications iOS (if needed) | 4 | **5** | **5** | 1 |
| Push notifications Android | **5** | **5** | **5** | 4 |
| Offline seeding (SQLite/IndexedDB) | **5** | 4 | 4 | 3 (IndexedDB only, evictable) |
| Volume PTT Android | 4 (small plugin) | **5** | 4 | 2 |
| Volume PTT iOS | 1 (private-ish hack) | 1 | 1 | 0 |
| Reuse current web code | **5** | 1 | 0 | **5** |
| **Weighted fit for *this* project** | **winner** | mid | mid | iOS killer flaws |

**Why PWA fails for this case:**
- iOS PWA `getUserMedia` works in 16.4+ but only inside the installed PWA, with prompt-flow quirks reported by many. The mic problems in Safari/Chrome — same engine, same risks.
- iOS PWA notifications: only 16.4+, only when installed to home screen, no background scheduling primitives.
- Volume buttons: zero hope in any browser context on iOS, and intercepting them on Android requires you to be the foreground page with focus — fragile.
- IndexedDB quota eviction on iOS is unpredictable; SQLite via a Capacitor plugin is more durable for the "seed pack" use case.

## Hard questions answered

**iOS mic inside Capacitor's WKWebView — does it work?**
Yes, on iOS 14.5+. Required pieces:
- `NSMicrophoneUsageDescription` in `Info.plist` with a human-readable string.
- `WKWebViewConfiguration.allowsInlineMediaPlayback = true` and `mediaTypesRequiringUserActionForPlayback = []` (Capacitor sets these by default).
- Capacitor's `App` plist permission entry.

Recommended path: don't rely on WebView `getUserMedia`. Use `@capacitor-community/voice-recorder`. It records via native AVAudioSession on iOS, returns base64/m4a, you POST it the same way the current code POSTs the webm blob. Whisper accepts m4a/aac. This sidesteps the entire class of WebView audio bugs and gives one code path for both OSes.

**Offline seeding — where does it live, how is it scheduled, what's online-only?**

Storage: **SQLite on-device** via `@capacitor-community/sqlite`. Mirror these tables from the server's `schema.sql`:
- `skills` (read-only cache of the canonical YAML-derived rows)
- A new `seed_pack` table (or reuse `attempts` with a `seeded=1` flag): pre-generated `{prompt_text, expected_answer, skill_id, tolerance_rule, parameters, tts_audio_uri}` rows.
- `attempts_outbox` queue for offline-recorded answers awaiting upload.
- Local mirror of `skill_state` for the scheduler to read offline.

Server work (new): `GET /seed-pack/{user_id}?n=200` that:
1. Asks the existing scheduler (`server/scheduler.py`) to pick the next ~200 problems weighted by mastery.
2. Calls the existing generators to produce concrete `(prompt_text, expected_answer, params)` tuples.
3. Pre-renders TTS for each prompt (TTS is already SHA-cached in `server/tts.py`, so this is mostly cache hits).
4. Returns a JSON manifest + a tarball/zip of audio files (or base64-inline; pick by size — likely tarball).

Scheduling "early in the day": local notification at e.g. 6 AM triggers a JS handler that calls `/seed-pack` over Wi-Fi. If it fails (no network), retry on next foreground. Don't background-fetch — too platform-specific to justify the work for one user.

Online vs offline feature map:

| Feature | Mode | Why |
|---|---|---|
| Drill session: pull next problem | **Offline** | Reads from `seed_pack` table |
| Play prompt audio (TTS) | **Offline** | Pre-rendered in the seed pack |
| Record answer audio | **Offline** | Native mic + local file |
| Grade the answer | **Mixed** | Local string + tolerance check works for ~all skills (tolerance rules are simple JSON); STT for voice answers requires upload. **Offline fallback: typed answer.** Or buffer audio and grade on reconnect. |
| Generate narrative feedback (`gpt-4o-mini`) | **Online only** | Show "feedback available when online" placeholder |
| Update mastery / `skill_state` | **Offline (local)**, reconciled on sync | Port `mastery.py` logic to JS or recompute server-side from synced attempts |
| Leaderboard / profile / diagnosis | **Online** | Live server query, cache last result |
| Managed-agent planning + memory | **Online only** | Inherently server-side |

**Anything forcing AI on-device?** No. Whisper, gpt-4o-mini, TTS — all stay server-side. The only "on-device intelligence" is grading (deterministic tolerance check) and mastery update (arithmetic). Both are tiny ports of existing Python.

**Notifications — local vs push, per platform.**
- **Local notifications**: `@capacitor/local-notifications` covers iOS and Android. No server required, no FCM/APNs setup, no certificates. **Use this for the morning nudge and "haven't drilled today" reminders.** This is 80% of the stated need.
- **Push notifications** (server-initiated): would require FCM project (Android) + APNs key (iOS Developer Program: $99/yr for a real cert, or 7-day rotating sideload certs without). Skip unless/until the managed-agent decides to ping mid-day with something earned. Add later via `@capacitor/push-notifications`; doesn't change architecture.

## Phased plan

Effort is calendar-loose: assume part-time on a personal project. "S/M/L" ≈ 1-2 / 3-5 / 6-10 working sessions.

### Phase 0 — De-risk spike (S) ← do this first
**Goal:** kill the riskiest unknown before committing.
- Init bare Capacitor over current `web/` (`npm init @capacitor/app`, `npx cap add ios`, `npx cap add android`).
- Sideload to iPhone (free Apple ID + Xcode, 7-day cert is fine) and Android device.
- Confirm: mic permission prompt works, recording uploads, Whisper transcribes m4a (iOS) correctly.
- Confirm: PWA service worker plays nice or gets disabled inside WebView (Capacitor docs).
- **Gate:** if iOS audio is flaky even with `voice-recorder` plugin, reconsider scope. Otherwise, ship it.

### Phase 1 — Capacitor shell + backend hardening (M)
- Capacitor project committed; `webDir` points at existing `web/`.
- Add `fastapi.middleware.cors.CORSMiddleware` to `server/main.py` (open to `capacitor://localhost` + Tailscale host).
- Add a Settings screen storing API base URL in `@capacitor/preferences`, read on every fetch (today the code assumes same origin — `web/app.js` uses bare paths).
- Wrap the Mac mini server in a **launchd plist** so it auto-starts at login and respawns on crash. Move the FD-limit `_raise_fd_limit` to a launchd `SoftResourceLimits` entry as belt-and-suspenders. **This independently solves the "restart server" pain.**
- Plugins installed: `@capacitor/preferences`, `@capacitor/local-notifications`, `@capacitor-community/sqlite`, `@capacitor-community/voice-recorder`.

### Phase 2 — Native audio capture (S)
- Replace `MediaRecorder` block in `web/app.js:258-315` with `VoiceRecorder.startRecording()` / `stopRecording()`. Keep same multipart upload path.
- iOS `Info.plist`: `NSMicrophoneUsageDescription`. Android `Manifest`: `RECORD_AUDIO`.
- Update `server/stt.py` to accept m4a in addition to webm if Whisper needs a hint.

### Phase 3 — Offline seeding (L) ← the main feature
- Server: new endpoint `GET /seed-pack/{user_id}` (see "Offline seeding" above). Reuse `server/scheduler.py`, generators, and `server/tts.py`.
- Client: SQLite schema for `skills`, `seed_pack`, `attempts_outbox`, `skill_state`. Migration runner.
- Client: rewrite session loop in `web/app.js` to:
  - Detect online state (`@capacitor/network`).
  - Offline: read next problem from `seed_pack`, play bundled audio, capture answer, write to `attempts_outbox` with timestamps.
  - Online: existing flow, with attempts also written to outbox-then-flush so the code path is one.
- Sync: on reconnect, `POST /session/attempts/bulk` flushes outbox; server runs Whisper on any audio-only attempts and updates `skill_state`.
- Grading logic: port the tolerance check from `server/` (it's a small piece of code) to JS so offline grading is immediate. Server still re-grades on sync as source of truth.
- AI feedback gracefully degraded offline: store "pending" marker, request feedback on next sync.

### Phase 4 — Local notifications (S)
- One screen of UI to schedule "morning seed-pack at 6 AM" + "if no drill by 6 PM, nudge."
- Hook into `@capacitor/local-notifications` schedule API. iOS will prompt for permission at first schedule.

### Phase 5 — Volume-button PTT (M, optional)
- Android: ~30 LOC `MainActivity` override of `onKeyDown(KEYCODE_VOLUME_UP/DOWN)`. Emit `pttPress`/`pttRelease` over a tiny Capacitor plugin bridge. The existing keydown handler hooks into the same callbacks.
- iOS: `AVAudioSession.outputVolume` KVO observer in a Capacitor plugin. Works while app is foreground. Document the caveat: Apple may reject if shipped to App Store; fine for personal sideload.

### Phase 6 — Managed-agent layer (XL, independent track)
This is the deep Python work, the actually interesting bit. Doesn't depend on or block any of the above — it's all server-side.
- Add an `agent/` package under `server/`. Per-user state: extend `skill_state` or add `agent_memory` table (rolling notes on weaknesses, narrative summaries, last-N-session digests).
- Build a small loop on top of the existing `Orchestrator`: planner picks the next *macro-goal* (e.g. "tighten regression on two-digit subtraction"), tactically falls back to the scheduler for problem selection within the goal, and writes a memory entry after each session.
- Expose `POST /agent/plan/{user_id}` and `GET /agent/memory/{user_id}`. Use them in the online session UI. Don't touch the offline path — offline stays scheduler-driven.
- Use the Anthropic API for the agent loop if you want tool-use + memory primitives that fit naturally; OpenAI is already used for STT/TTS/feedback — mixing providers is fine, the two domains are independent.

## Riskiest unknown + spike

**The single riskiest unknown is iOS audio capture reliability inside Capacitor.** Everything else (CORS, SQLite, notifications, Android volume keys, offline seeding) is well-trodden and the code is small enough that rework is cheap. If iOS audio is unreliable or laggy on a real device, the whole "fix iOS mic" requirement collapses and you'd reconsider RN.

**Spike to de-risk (1 day):**
1. `npm init @capacitor/app`, `npx cap add ios`, point `webDir` at a tiny scratch HTML page with one record button.
2. Install `@capacitor-community/voice-recorder`. Wire start/stop/upload to a curl-pasted endpoint that just saves the blob.
3. Open in Xcode, sign with a free Apple ID, deploy to iPhone.
4. Record 5 short clips. Confirm: permission prompt appears, recording starts immediately (no 1-2s lag), playback sounds clean, file uploads, Whisper transcribes accurately.
5. **Gate:** if good → proceed to Phase 1. If audio is laggy / Whisper accuracy drops vs. webm → evaluate raw `getUserMedia` in WKWebView as fallback; only if both fail, reconsider stack.

## Tradeoffs and blunt notes

- **iOS volume PTT is effectively not happening.** Accept "big on-screen hold-to-talk button" as the iOS UX. Don't waste effort on AVAudioSession volume KVO unless it's a personal itch.
- **No auth today.** A native app on cellular reaching the Mac mini over Tailscale is fine for personal use. Don't add auth until this is ever exposed beyond yourself. CORS open to the specific Tailscale hostname is sufficient.
- **The service worker may need to be disabled inside the Capacitor WebView.** Capacitor serves from `capacitor://localhost`; SW scope semantics can get weird. Plan to gate SW registration on `!Capacitor.isNativePlatform()`. Nothing is lost — the app shell is cached by the WebView's normal disk cache.
- **Pre-rendered TTS bloats the seed pack.** 200 problems × ~50KB of cached TTS audio ≈ 10MB per morning sync. Acceptable on Wi-Fi. Consider on-device TTS (`@capacitor-community/text-to-speech`) as a fallback for prompts whose audio failed to bundle.
- **Don't build "real" sync** (CRDT, vector clocks, merge resolution) yet. The scheduler is the source of truth for what to drill, the server is the source of truth for mastery, attempts are append-only. A simple "flush outbox, server is authoritative for `skill_state`" model is enough.
- **The managed-agent vision is the actual interesting work.** The mobile migration is plumbing. Don't let the mobile track block the agent track — they can run in parallel; the agent ships behind an online-only "Coach" screen.
- **"Everything else is theatre."** The single non-negotiable success metric here is: do you actually drill on the train? Phases 0-4 deliver that. Phase 5 is sugar. Phase 6 is the real intellectual investment.

## Verification

End-to-end test once the migration phases complete:

1. **Connectivity loss test:** Drill a session on Wi-Fi. Toggle airplane mode mid-session. Confirm the next prompt comes from the seeded pack, audio plays, answer is captured, attempt sits in outbox.
2. **Reconnect test:** Disable airplane mode. Confirm outbox flushes within 30s, `skill_state` updates server-side (visible on Profile screen).
3. **Morning seed test:** Schedule the local notification for +2 min from now. Background the app. Confirm notification fires, tapping it triggers sync, new seed pack lands in SQLite.
4. **iOS mic test:** Cold install on a phone with the Capacitor app *deleted and reinstalled*. Confirm permission prompt fires on first record, recording is immediate, Whisper transcription accuracy is comparable to today's Tailscale-Safari baseline (eyeball 10 attempts).
5. **Android volume PTT:** Drill with volume keys, confirm press/release map to record/stop.
6. **Server-restart test:** `sudo killall -9 python` on the Mac mini; confirm launchd restarts within 5s and the next API call from the phone succeeds.
