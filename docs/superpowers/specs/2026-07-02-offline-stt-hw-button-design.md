# Offline voice answers + hardware PTT — design

Date: 2026-07-02 · Status: approved (artifact brief + conversation)

## Goal

Run a drill on the Razr cover screen fully offline, answering by holding
volume-down and speaking. No touch needed after session start.

## Decisions (user-approved)

- **On-device STT is fallback-only.** Online keeps server STT
  (`gpt-4o-mini-transcribe`). Offline uses Android's on-device recognizer via
  `@capacitor-community/speech-recognition`.
- **Volume-down = press-and-hold-to-answer**, armed only while a question is
  answerable; passes through to normal volume control otherwise. Volume-up is
  never intercepted. Power button rejected (OS-reserved).
- **Keep-awake during an active session** (`@capacitor-community/keep-awake`).
  True screen-off operation is out of scope (v2 spike: MediaSession
  VolumeProvider hack).
- One APK rebuild ships all native pieces; all behavior lives in `web/` JS and
  iterates via Capgo OTA afterward.

## Components

1. **Native key hook** — tiny inline Capacitor plugin (`PttKeys`) registered in
   `MainActivity`: JS calls `setArmed(true/false)`; while armed,
   `dispatchKeyEvent` swallows `KEYCODE_VOLUME_DOWN` and emits `pttDown` /
   `pttUp` events to JS. JS wires these to the existing `startRecording` /
   `stopRecording` (replacing the dead `AudioVolumeUp/Down` keydown handlers at
   `web/app.js:1886-1911`, which Android WebView never fires).
2. **Offline voice answers** — offline sessions currently hide `#btn-ptt` and
   show the typed form (`web/app.js:694-697`). Change: show PTT too (typed form
   stays as fallback). Press → `SpeechRecognition.start({partialResults:true})`
   (native recognizer owns the mic; no `MediaRecorder`/VU meter offline);
   release → stop → transcript → existing `buildOfflineAttempt` path
   (`parseTypedAnswer` → `gradeAnswer` → SQLite → later sync). Add a
   words-to-numbers fallback in `parseTypedAnswer` ("forty two" → 42). If the
   recognizer is unavailable on-device, keep today's typed-only behavior.
3. **Keep-awake** — `keepAwake()` on session start (online and offline),
   `allowSleep()` on session end/abort.

## Error handling

- Recognizer error/empty transcript offline → status message, question stays
  answerable (typed form untouched).
- `notifyAppReady` / OTA flow untouched; plugin absence (old APK + new bundle)
  must fail soft: feature-detect `Capacitor.isPluginAvailable` before use.

## Testing

- Unit: words-to-numbers parser cases (digits, words, "skip", garbage).
- Manual on device: volume-down PTT online + offline; airplane-mode drill by
  voice, graded locally, syncs on reconnect; screen stays awake through a
  session; old-APK-new-bundle soft-fail.

## Out of scope

Screen-off/pocket operation, power button, earbud MediaSession, iOS.
