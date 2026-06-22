# WebView mic capture: intermittent dead-track strategy

**Status:** parked (2026-06-22). Detection + robustness shipped; the structural
fix below is deferred until the telemetry says it's still needed.

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

## Strategy ladder (cheapest / highest-leverage first)

1. **Tier 1 — acquire the mic once per session, not per question.** ⭐ Try first.
   Open the stream at session start, keep it alive, `start()`/`stop()` the
   `MediaRecorder` on it per question, release on session end. Removes the
   acquire/release race entirely. Contained change; bonus: lower latency, no
   per-question permission churn. Tradeoff: mic-in-use indicator stays on for the
   session, slightly more battery.
2. **Tier 1.5 — health check + auto re-acquire.** Before recording, verify
   `track.readyState === "live" && !track.muted`; re-acquire if not. Pairs with
   `isDeadCapture` so a dead stream silently re-opens instead of only warning.
3. **Tier 2 — native capture plugin** (Capacitor mic/voice-recorder, native
   `AudioRecord`). More robust; bypasses MediaRecorder. But re-architects: lose
   the WebAudio meter (need native level callbacks), change upload format, add a
   native dep. Only if Tier 1 is insufficient.
4. **Tier 3 — accept + lean on fallbacks** (typed answer, Skip). Reasonable floor
   for a personal app.

## Recommended next step

Ship Tier 1, then **let the `energy`/`bytes` telemetry judge it** over a few
sessions. If the empty/dead rate drops to ~0, stop. If it persists above the ~7%
baseline, that's the signal it's deeper WebView jank → escalate to Tier 2.
