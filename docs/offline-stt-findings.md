# Offline STT: findings & proposal

2026-07-03 · Status: diagnosed, fix not started (user hold)

## Symptom

Offline voice answers are slow and unreliable — nothing like the online
(OpenAI `gpt-4o-mini-transcribe`) experience. Recognition often returns
nothing and the drill feels laggy.

## Evidence (voice_offline_timing telemetry, session offline:1783049507546)

Synced 2026-07-02 23:33, seeds 112–116, ~10 press/release cycles:

- `partial_count: 0` and empty transcripts on most attempts — the recognizer
  returned nothing at all.
- `end_signal: "cap"` almost everywhere: no end-of-speech signal ever
  arrived, so every release waited the full 2 s timeout before grading.
- The two attempts that produced text had `first_partial_ms` ≈ 1385–1505.
- Recognizer start is not the problem (`press_to_ready_ms` 3–9 ms), and the
  session-entry fixes work (no duplicate seed served, double-taps blocked).

## Diagnosis

`@capacitor-community/speech-recognition@7.0.1` never sets
`RecognizerIntent.EXTRA_PREFER_OFFLINE`, so in airplane mode Google's speech
service attempts **online** recognition first and mostly fails. This is not
a language-pack issue — the offline pack is installed (English US, 93 MB,
v3072, verified by screenshot 2026-07-03). Known secondary defects in the
same plugin, confirmed against its vendored source during review:

- `stop()` never resolves its Capacitor call on success (we already
  fire-and-forget around it — web/app.js comment near `offlineVoiceRelease`).
- `listeningState: "stopped"` is only emitted from `onEndOfSpeech`, which
  Android doesn't fire for a manual PTT release without detected speech —
  the reason for the 2 s cap fallback.
- A fresh `SpeechRecognizer` is built per press (cold start each time).

## Proposal: replace the plugin with a small in-repo one

Add `OfflineSttPlugin.java` (~150 lines) next to the existing
`PttKeysPlugin` in `android/app/src/main/java/com/psylabs/feynman/`,
registered in `MainActivity`. Same JS surface we already consume
(`available()`, `start({language, partialResults})`, `stop()`, events
`partialResults` / `listeningState`), plus:

1. `EXTRA_PREFER_OFFLINE = true`, and
   `SpeechRecognizer.createOnDeviceSpeechRecognizer()` on API 31+ — the
   actual fix.
2. `stop()` resolves properly; emit an explicit `stopped` event on
   `onResults`/`onError` too, so the JS 2 s cap becomes a rare fallback
   instead of the common path (`end_signal` telemetry will verify).
3. Optionally keep the recognizer instance warm per session.

JS change is small (swap the plugin handle in web/app.js, keep telemetry).
Cost: native change ⇒ one APK rebuild + sideload; the community plugin dep
can then be dropped. Rejected alternatives: patching `node_modules` (not
durable), whisper.cpp in-app (40–150 MB model, big lift — escalation path if
on-device Google quality still disappoints for short numeric answers).

## Also open

- `boot_timing` has still never landed — no cold start while online yet;
  possible secondary gap: at boot the SQLite queue may not be ready when the
  event fires (queueClientEvent swallows the failure). Check after the next
  online cold start.
- `/client-log/bulk` accepts unbounded arrays (matches existing `/sync`
  pattern; fine single-user, cap someday).
- Watch `end_signal` distribution after the plugin swap; if "cap" persists,
  reduce the 2 s cap using real `release_wait_ms` data.
