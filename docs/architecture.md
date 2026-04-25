# Feynman MVP — Architecture

> Companion to [mvp-requirements.md](mvp-requirements.md). This doc says what runs where, how a turn flows through the system, and what the code layout looks like. After this, we build.

---

## 1. Topology

Everything runs on the Mac. A local Python server holds the state, the SQLite file, and the API keys; it serves a static web app to a browser tab and exposes a small HTTP + SSE API the browser talks to. The browser handles audio playback, the push-to-talk button, and rendering. Outbound HTTPS goes only to OpenAI for TTS and Whisper.

No auth, no multi-user, no public network exposure. The server binds to localhost.

## 2. Diagram

```mermaid
flowchart TB
  subgraph Browser["Browser tab — localhost:PORT"]
    UI["Session UI<br/>(start • prompt • push-to-talk • review)"]
    Debug["Debug pane<br/>(live event stream)"]
  end

  subgraph Server["Local server — Python + FastAPI"]
    Orch[Session orchestrator]
    Sched[Scheduler]
    Gen[Question generator<br/>templated]
    Grade[Grader<br/>deterministic]
    Mast[Mastery updater]
    TTS[TTS adapter]
    STT[STT adapter]
    Parse[Number parser]
    Bus[Event bus]
  end

  subgraph Storage["Local files"]
    DB[("SQLite<br/>skills • sessions<br/>attempts • skill_state")]
    Log[("JSONL log<br/>logs/YYYY-MM-DD.jsonl")]
    Yaml[("skills.yaml<br/>hand-curated definitions")]
  end

  subgraph External["External APIs"]
    OAI["OpenAI<br/>TTS + Whisper STT"]
  end

  UI <-- "HTTP" --> Orch
  Debug <-. "SSE" .- Bus

  Orch --> Sched
  Orch --> Gen
  Orch --> TTS
  Orch --> STT
  Orch --> Parse
  Orch --> Grade
  Orch --> Mast

  Sched --> DB
  Mast --> DB
  Orch --> DB
  Gen --> Yaml

  TTS --> OAI
  STT --> OAI

  Sched -.-> Bus
  Gen -.-> Bus
  Grade -.-> Bus
  Mast -.-> Bus
  TTS -.-> Bus
  STT -.-> Bus
  Parse -.-> Bus
  Orch -.-> Bus
  Bus --> Log
```

Solid lines are request paths. Dotted lines are events flowing into the bus, which fans out to both the SSE stream (debug pane) and the JSONL log file.

## 3. Turn Lifecycle

A single question is the unit of orchestration. Everything else is composition over this.

1. **Browser asks for next question.** UI POSTs to `/session/next`.
2. **Scheduler picks a skill.** Reads `skill_state` from SQLite, computes priority for each candidate, samples the top of the queue with weighted randomness. Emits an event with the priority table and the chosen skill.
3. **Generator builds the problem.** Loads the skill's templates from `skills.yaml`, samples parameters scaled to current mastery, renders the prompt text, computes the expected answer. Returns prompt + answer + parameters.
4. **TTS adapter produces audio.** Server calls OpenAI TTS, gets back an MP3, returns it to the browser along with the prompt text and a `question_id`. Audio length is recorded for `prompt_audio_ms`.
5. **Browser plays audio.** When playback ends, frontend stamps `prompt_end_ts` (client-side; the server doesn't see this until later). Push-to-talk button activates.
6. **User answers.** Press records `onset_ts` and starts capturing mic audio. Release records `resolution_ts` and stops capture.
7. **Browser submits.** POSTs to `/session/submit` with the audio blob, the three timestamps, and the `question_id`.
8. **STT adapter transcribes.** Server sends audio to Whisper, gets text back.
9. **Parser extracts a number.** Deterministic, prompt-aware (knows the expected unit and magnitude).
10. **Grader judges.** Looks up the skill's tolerance rule, compares parsed value to expected, returns correctness and error magnitude. Skips are recognized via known phrases before parsing.
11. **Persistence.** A row is written to `attempts` with every field from Section 7 of the operating spec.
12. **Mastery updater recomputes.** Reads the last 10 attempts for that skill, recomputes accuracy, median latency, mastery, and priority. Writes to `skill_state`.
13. **Response returned.** Browser shows the result briefly, requests the next question, or ends the session.

Every step emits a structured event into the bus.

## 4. Components

**Session orchestrator** is the only thing the HTTP layer talks to. It composes the other modules into the turn lifecycle and owns no state of its own — everything that needs to persist goes through Storage.

**Scheduler** is pure: given the current `skill_state` table, it returns a skill id and a difficulty signal (a number that scales parameter ranges). It does not write anything.

**Question generator** loads templates from `skills.yaml`, picks one for the chosen skill, samples parameters, and renders. It produces both the prompt string and the expected answer using the same code path, so they can never drift.

**Grader** is deterministic and small. It knows nothing about templates or skills as a whole — it just gets `(parsed_value, expected_value, tolerance_rule)` and returns a verdict.

**Mastery updater** runs after every attempt. It is the only writer of `skill_state`, and it always derives state from `attempts` — never from itself — so the cache can be rebuilt at any time.

**TTS / STT / Parser** are thin adapters. Each one logs its input, output, and external latency. The number parser is the only one with real logic of its own; TTS and STT are mostly request shaping.

**Event bus** is an in-process pub-sub. Every component emits structured events; the bus fans them out to two sinks: the SSE endpoint that the debug pane subscribes to, and the JSONL writer that appends to today's log file. Events are stamped with monotonic timestamps so cross-component sequences read cleanly.

## 5. Storage

A single SQLite file at `data/feynman.db`. Four tables, all defined in one schema file checked into the repo:

`skills` is loaded from `skills.yaml` on app start. The YAML is the source of truth; the table is the queryable form.

`sessions` records start time, end time, and any session-level defaults.

`attempts` is the ground truth of everything that has happened. Every other piece of derivable state can be rebuilt from this table.

`skill_state` is a derived rollup. Treated as a cache.

The JSONL log at `logs/YYYY-MM-DD.jsonl` is separate from the database. One event per line, structured, rotates daily. The log is the diagnostic surface — when something looks wrong, the log answers what happened.

## 6. External Services

OpenAI for both TTS (`tts-1`) and STT (`whisper-1`). One API key, one provider, two endpoints. Both calls are awaited synchronously inside a turn — TTS before the user answers, STT after. Round-trip latency on each is logged and shown in the debug pane.

No LLM in MVP. The Anthropic SDK enters when grounding lands and we need to render naturally-phrased problems from real-life context.

## 7. Tech Choices

**Python + FastAPI** for the server. The audio and AI ecosystem is first-class in Python, FastAPI gives HTTP and SSE without ceremony, and the type hints + Pydantic models make the API surface inspectable.

**Vanilla JS, HTML, CSS** for the browser. Three screens (start, in-session, review) plus a debug pane. A framework would be overhead. The codebase stays small and easy to read.

**SQLite** for storage. Justified in the operating spec — file-based, queryable, the file itself is a diagnostic surface via any SQL viewer.

**Server-Sent Events** for the debug stream. One-way server-to-browser, no library needed on either end, drops cleanly on reload. WebSockets would be more capability than the use case needs.

**OpenAI TTS + Whisper** for voice. Simplest cloud path, high quality, push-to-talk means no streaming STT complexity.

## 8. Project Layout

```
feynman/
├── docs/
│   ├── personal-cognitive-trainer-plan.md
│   ├── mvp-requirements.md
│   └── architecture.md
├── server/
│   ├── main.py            # FastAPI app, route definitions
│   ├── orchestrator.py    # turn lifecycle
│   ├── scheduler.py
│   ├── generator.py       # template rendering
│   ├── grader.py
│   ├── mastery.py
│   ├── parser.py          # spoken-number → numeric
│   ├── tts.py
│   ├── stt.py
│   ├── storage.py         # SQLite access
│   ├── events.py          # event bus + JSONL writer
│   └── schema.sql
├── web/
│   ├── index.html
│   ├── app.js
│   ├── debug.js
│   └── styles.css
├── skills.yaml            # skill definitions
├── data/
│   └── feynman.db         # SQLite (gitignored)
├── logs/
│   └── YYYY-MM-DD.jsonl   # daily logs (gitignored)
├── pyproject.toml
└── README.md
```

## 9. What We Are Not Building

No build pipeline for the frontend. No bundler, no transpiler, no test framework yet. No Docker. No process manager. No migrations system — schema changes are hand-applied during MVP. No deployment story — this runs on the Mac, started from the terminal.

These are deliberate omissions. They become real questions later; in the MVP they would only slow us down and obscure what's actually working and what isn't.

## 10. Starting the App

`python -m server.main` starts the FastAPI server on a fixed local port. The server serves the static frontend from `web/` and the API from the same origin. Open the browser tab, click start, talk.
