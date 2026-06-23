# WebView mic capture: intermittent dead-track strategy

**Status:** Tier 1 shipped (2026-06-23). Detection + robustness shipped first;
the app now also keeps one mic stream open for the session instead of
acquiring/releasing it for every question.

## Symptom

Voice answers intermittently fail: the recorded clip is a header-only WebM
(~110 bytes) or near-silence, which STT returns as empty/"skip". It fails for a
whole session, then recovers on its own ("worked, broke for a session, worked
again"). Android WebView only (Motorola Razr 2025, Android 16, Chrome 149 wv).

## Root cause (evidence-based)

The loudness meter (`peak_rms`) and the recorded `bytes` fail **and recover
together** — one dead-capture bug, not a separate "meter never resumes" issue.
The meter worked fine on 6-21 with the same code that has no `AudioContext.resume()`,
so the missing resume is a red herring.

Prime suspect, and it's in our code, not inherent WebView jank: we call
`getUserMedia` at the **start of every question** and fully `track.stop()` at the
end (`web/app.js`, `startRecording`/`onstop`). On Android this maps to native
`AudioRecord`/AudioFlinger acquire→release. Doing that rapidly many times per
session races; if one release hasn't torn down before the next acquire, you get a
live-but-silent track that stays dead until the audio stack resets (backgrounding,
GC, time). "Dead for a session, then recovers" is the fingerprint of that race.

Baseline empty rate ~7% (`answer.no_audio` on 6-19/6-20, before metering was even
on-device — so it predates and is independent of the loudness meter).

## What's already shipped (commit 9c510e9)

- `mediaRecorder.start(250)` — flush a chunk every 250ms so a momentary encoder
  stall costs one chunk, not the whole clip.
- `isDeadCapture(meta)` guard — when a clip captured no signal (`bytes<=200` or
  `peak_rms<0.01`), re-prompt locally ("Mic didn't catch that — try again")
  instead of shipping silence to STT. Skip/typed remain available.
- `client_meta.energy` telemetry (peak/mean/floor RMS, voiced_ms) — the empirical
  judge for whether further work is needed.

These reduce stalled-encoder empties and stop silent clips becoming phantom
skips. They do **not** revive a fully-dead track mid-session.

## What's now shipped (2026-06-23)

- Tier 1: acquire the mic once at session start, reuse that stream for each
  per-question `MediaRecorder`, and release only on session end/navigation.
- Tier 1.5: before recording, reacquire if the stream is missing, inactive,
  ended, or muted.
- Client-side capture diagnostics are posted to `/client-log` and therefore land
  in the normal `logs/YYYY-MM-DD.jsonl` stream and debug pane:
  `client.mic_acquire_start`, `client.mic_acquired`, `client.mic_released`,
  `client.mic_track_muted`, `client.mic_track_unmuted`,
  `client.mic_track_ended`, `client.recorder_error`, and
  `client.capture_dead`.
- Uploaded `answer.received` metadata now includes `client_meta.stream` with
  track state and non-identifying audio settings such as sample rate/channel
  count/echo-cancellation flags when the WebView exposes them.

## Strategy ladder (cheapest / highest-leverage first)

1. **Tier 1 — acquire the mic once per session, not per question.** ✅ Shipped.
   Open the stream at session start, keep it alive, `start()`/`stop()` the
   `MediaRecorder` on it per question, release on session end. Removes the
   acquire/release race entirely. Contained change; bonus: lower latency, no
   per-question permission churn. Tradeoff: mic-in-use indicator stays on for the
   session, slightly more battery.
2. **Tier 1.5 — health check + auto re-acquire.** ✅ Shipped. Before recording, verify
   `track.readyState === "live" && !track.muted`; re-acquire if not. Pairs with
   `isDeadCapture` so a dead stream silently re-opens instead of only warning.
3. **Tier 2 — native capture plugin** (Capacitor mic/voice-recorder, native
   `AudioRecord`). More robust; bypasses MediaRecorder. But re-architects: lose
   the WebAudio meter (need native level callbacks), change upload format, add a
   native dep. Only if Tier 1 is insufficient.
4. **Tier 3 — accept + lean on fallbacks** (typed answer, Skip). Reasonable floor
   for a personal app.

## Recommended next step

Field-test with the Moto Razr + Bluetooth headphones. Check:

- `client.capture_dead`: client rejected a dead/near-silent clip before upload.
- `answer.received.client_meta.stream`: whether Android exposed muted/ended
  track state or suspicious audio settings.
- `answer.stt_skip_retry` with healthy `bytes`/`energy`: valid-size audio still
  reached STT but was unusable.

If `client.capture_dead` or 110-byte uploads drop to ~0, Tier 1 fixed the
dead-track race. If valid-size Bluetooth clips still repeatedly transcribe as
`skip`, escalate to Tier 2 native capture.
