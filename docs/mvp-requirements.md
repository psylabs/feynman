# Feynman MVP — Operating Spec

> Companion to [personal-cognitive-trainer-plan.md](personal-cognitive-trainer-plan.md). This doc defines the behavior of the first working version: a Mac browser-tab app that runs short, voice-first mental math sessions with push-to-talk, measures performance precisely, and adapts what it asks next. Real-life grounding is **not** in this MVP — it's the next layer, and the data model is designed to slot it in cleanly.

---

## 1. Goal of the MVP

Build a self-contained trainer that proves three things:

| Hypothesis | What "proven" looks like |
| --- | --- |
| The voice loop is clean enough to trust. | Latency numbers are stable across sessions; the user is not fighting the mic. |
| The skill taxonomy carves the space well. | Mastery moves on real signal, not noise; weak spots stay surfaced until they actually improve. |
| The session feels like training, not a quiz. | The user voluntarily comes back. Sessions feel calibrated — not too easy, not punishing. |

If those land, grounding (calendar, receipts) becomes a content layer on top of a working engine. If they don't, no amount of grounding rescues it.

## 2. Scope

In: a browser-tab Mac app, voice-only, push-to-talk, single user, ~10 broad skills, deterministic grading, per-skill mastery and a simple recency-aware scheduler, end-of-session review, and a always-visible debug log.

Out: real-life grounding, passive triggers, mobile, multi-user UI, LLM-driven hints during a question, visual mode, polish.

## 3. The App Surface

A single browser tab. One big start button. Inside a session: the question text appears on screen, a push-to-talk button is the only control, and a fixed debug pane along the side or bottom streams every event in real time. After the session ends, a review screen replaces it.

No theming, no copy polish, no streaks. The point of MVP is that it works and that we can see exactly what it's doing.

## 4. The Session

A session is short on purpose — 3 to 5 minutes, 10 to 15 questions. Long sessions degrade signal: fatigue muddies latency and the user starts to dread them.

Each turn runs the same way. The system renders a prompt — TTS speaks it, the text appears on screen the moment speech ends. The push-to-talk button activates as soon as TTS finishes; that moment is `t=0` for latency measurement. The user holds the button, speaks the answer, releases. Press-down marks `onset_ts`; release marks `resolution_ts`. The transcript is parsed into a number, the deterministic grader judges it against tolerance, and the result is shown briefly before the next question. Skips ("skip", "don't know", "pass") are recognized and recorded as attempts with `skipped=true`.

The session ends after its target question count, or whenever the user stops it. A stopped session still records every attempt that ran.

## 5. Skills

This is the section that needs the most care. The wrong cut here costs us either an unmanageable taxonomy or a system that can't see real weakness patterns.

### The principle

A **skill** is a distinct mental routine — a kind of computation the user performs. Two problems exercise the same skill if they use the same routine with different numbers. Two problems exercise different skills only when the user has to think about them in a fundamentally different way.

Numbers and variations within the same routine are **parameters**, recorded on every attempt, not new skills. The data still tells us "the user is slow at 18% specifically" via filters on the attempt log — we don't need a `pct_18_of_n` skill to discover that.

### The starting set

Ten broad skills, intentionally coarse:

1. Addition
2. Subtraction
3. Times-table recall (factual retrieval)
4. Multi-digit multiplication (calculated)
5. Integer division
6. Percent of a number
7. Percent change and discount
8. Time arithmetic (durations, leave-by, elapsed)
9. Estimation and rounding
10. Unit conversion

This list is illustrative, not final. The systematic method below governs how it grows.

### Per-attempt parameters

Every attempt logs the parameters that defined that problem instance. For arithmetic skills: digit count of each operand, whether carry/borrow was required, magnitude. For percentages: percentage value, base value, "base ugliness" (round vs. awkward). For time: span type (within-hour, across-hour, multi-hour), AM/PM crossing. For estimation: target tolerance and operation type.

These parameters are the dimensions along which weakness is later detected and along which a skill can be split if the data demands it.

### How the taxonomy grows

The taxonomy expands when one of three things happens, and only then:

A parameter region inside an existing skill shows persistent, distinct performance — consistently slower or less accurate than the rest of the skill, sustained over enough attempts to rule out noise. That region becomes a candidate for splitting into its own skill. The split is justified because the data implies a different mental routine is at play.

A real-life situation produces a question the user couldn't compute, logged manually as a candidate. If the underlying routine isn't already covered, it becomes a new skill.

A new domain opens up — most often when grounding lands and surfaces problems like tip-with-tax, route-time math, or split-the-bill-with-shared-items. Each such domain enters as a candidate skill and is added if it isn't reducible to one already on the list.

The point of this rule is that the taxonomy reflects observed reality rather than anticipated need. We start coarse and let the system tell us where to split.

## 6. Voice and Push-to-Talk

Push-to-talk is the MVP choice. It costs us a tiny amount of natural feel and gains us clean, unambiguous timing. The button being pressed marks the start of the answer; the button being released marks the end. There is no VAD, no silence threshold, no self-correction window — if the user wants to retry, they hit the button again before submitting and the latest utterance wins.

TTS speaks the question. STT runs on the captured audio after release. The transcript is parsed into a number using a deterministic parser that handles the common spoken forms — "twenty-five", "a hundred and twenty", "twenty-five fifty", "five and a half", and so on. Prompts state the expected unit ("…to the nearest dollar", "…in minutes") so the parser has a clear target. Edge cases prefer the interpretation matching the prompt's expected magnitude.

## 7. Measurement

Every attempt records:

| Field | Meaning |
| --- | --- |
| `prompt_text` | Exact rendered question |
| `prompt_audio_ms` | Length of TTS playback |
| `prompt_end_ts` | When TTS finished — t=0 for latency |
| `onset_ts` | Push-to-talk button press |
| `resolution_ts` | Push-to-talk button release |
| `onset_latency_ms` | `onset_ts − prompt_end_ts` |
| `resolution_latency_ms` | `resolution_ts − prompt_end_ts` |
| `raw_transcript` | Full STT output |
| `parsed_answer` | Numeric value the grader judged |
| `expected_answer` | Ground truth |
| `correct` | Boolean, after tolerance |
| `error_magnitude` | Signed difference |
| `skipped` | True if user skipped |
| `skill_id` | Which skill |
| `parameters` | JSON blob of the problem's parameters |
| `session_id` | Foreign key |
| `position_in_session` | Index, for fatigue analysis |
| `notes` | Free-text slot for qualitative observations |

Onset and resolution stay separate on purpose. Onset says how hard retrieval was; resolution says how fluent the whole answer was.

## 8. Grading

Correctness is decided by deterministic rules. The LLM never grades. Tolerance is a per-skill property of the skill definition, so the grader stays simple and the rules are inspectable:

- Pure arithmetic is exact.
- Percentages and tips: ±$1 or ±1% of the expected value, whichever is larger.
- Time arithmetic: exact in minutes, with ±1 minute allowed only on multi-hour spans.
- Estimation: explicit per-skill ranges (e.g., within 10% of the true product) — the whole point of the skill is the tolerance.
- Unit conversion: ±2% of expected, to allow for natural rounding.

## 9. Adaptation

Three small pieces.

**Mastery (per skill)** is computed from the last 10 attempts:

```
accuracy = mean(correct over last 10)              // 0..1
speed    = clamp(target_latency_ms / median_latency, 0, 1)
mastery  = 0.7 * accuracy + 0.3 * speed
```

A skill is "fluent" at `mastery ≥ 0.85` sustained over 5+ attempts. Mastery decays slowly with disuse — that decay is the cognitive-decline-fighting mechanism.

**The scheduler** picks the next skill by computing a priority for each:

```
priority = (1 − mastery) × recency_factor(days_since_last_seen) × small_random_jitter
```

`recency_factor` starts low immediately after a skill is exercised and rises back over a few days. The next skill is sampled from the top of the priority queue with weighted randomness so sessions don't feel deterministic.

**Difficulty within a skill** is a parameter sample. The generator picks parameter ranges that scale with current mastery — easier numbers when mastery is low, harder ones as it climbs. No fixed buckets.

There is no warm-up / core / stretch composition rule in MVP. The scheduler picks each turn the same way. We can layer composition on later if sessions feel monotonous.

## 10. Storage

SQLite, single file on disk. No server, no migrations layer in MVP — just a schema file and a small data-access module. Three tables matter:

`skills` holds static definitions: id, parent concept, parameter schema, tolerance rule, target latency, the prompt template set. Hand-curated; lives in code or a checked-in YAML file that loads into the table on app start.

`sessions` holds one row per session: start time, end time, defaults.

`attempts` holds one row per question with every field listed in Section 7. This is the ground truth.

`skill_state` is a derived rollup — rolling accuracy, median latency, mastery, last-seen timestamp, current priority. It can always be rebuilt from attempts, so we treat it as a cache. Recomputed after each attempt for the affected skill.

The reason for SQLite over JSON files: querying. The first time we want "median resolution latency on percentages with awkward bases over the last two weeks," SQL turns it into a one-liner. A SQL viewer pointed at the file also doubles as the most reliable diagnostic surface there is.

## 11. Logging

The user must be able to see exactly what is happening at any moment. Two surfaces:

A **debug pane** in the app, always visible, streaming structured events in real time: TTS started/ended, button pressed/released, audio chunk sizes, partial and final transcripts, parsed value, grader decision (with the rule that fired and the tolerance applied), scheduler computation for the chosen skill (priority of top candidates and why this one was picked), any LLM call with prompt and response, costs and latencies on external calls.

A **log file** on disk, JSONL, one event per line, timestamped, sessioned. Rotates daily. Tailable from a terminal. Mirror of the debug pane plus anything the pane elides for readability.

Both are first-class: if something goes wrong, the answer should always be in one of them.

## 12. Review Screen

End-of-session, plain and direct: the question count, accuracy, and median resolution latency at the top; a per-question list with prompt, your answer, correct answer, latency, skill; a highlight on the slowest or shakiest one or two attempts; the skills whose mastery moved up or down today, with deltas. No streaks, no badges, no encouragement copy. The signal is the reward.

## 13. Cold Start

The first ~20 attempts have no real mastery signal. Every skill initializes at `mastery = 0.5` so they're equally eligible. The scheduler samples broadly across the ten skills in the first two or three sessions. Difficulty stays mid-range until each skill has at least three attempts, so we don't punish the user before the model knows them. After roughly 20–30 attempts the picture firms up and the scheduler behaves normally.

## 14. Open Questions

| Question | Why it matters |
| --- | --- |
| TTS voice and pacing | Affects how prompts feel; cadence subtly affects perceived difficulty. Try at least two voices early. |
| How to seed `target_latency_ms` per skill | Probably a fixed initial value per skill, then auto-tuned from the user's own data after N attempts. |
| Whether to show parsed answer to user before grading | Reassuring but might bias self-correction. Default off; revisit. |
| When to introduce the first skill split | Need a concrete rule for "persistent and distinct" before we hit it in real data. |
