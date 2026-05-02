# From Toy to Trainer

> A plan to close the gap between what Feynman *measures* and what Feynman *shows it understands*, and to ground sessions in the user's actual life — starting with Google Calendar.

This is a planning document. It does not implement; it sequences and justifies.

---

## 1. The honest diagnosis

The system already has a real diagnosis engine ([server/diagnosis.py](server/diagnosis.py)) and a diagnosis-first scheduler ([server/scheduler.py](server/scheduler.py)). It computes per-fact stats, identifies slowest facts, regressions, and drill priorities weighted by accuracy gap. That part is not toy-grade.

The reasons it *feels* like a toy:

1. **The diagnosis is invisible to the user mid-flow.** The post-session review is a generic attempts table. The user has no way to feel that the system noticed anything.
2. **Sessions are short and scattered.** 5 questions per drill, weighted-random over the top 8 priorities, no within-session clustering. Even when the scheduler is correct, a session of "7×8, then 89−47, then 18% of 60, then 6×7, then 2d+2d carry" reads as random.
3. **Inconsistent fact granularity.** Multiplication shows specific facts (`mul:6x7`); addition/subtraction show buckets (`add:2d+2d:c`). The profile presents both side-by-side, which is incoherent to the user even though it's principled in the diagnosis engine.
4. **Targets are constants, not calibrations.** `target_latency_ms` is a per-skill constant from `skills.yaml`. The research doc explicitly says these are starting points that should adapt per-user; they don't.
5. **Numbers are abstract.** Every problem comes from `random.randint` over a hardcoded range. The user has no relationship with `74 + 38`. The MVP plan calls grounding (calendar, receipts, recurring numbers) the *first* differentiator; nothing of it is built.
6. **No retention layer.** Mastered facts disappear from the priority list and never come back. The research doc's §1.4 (spaced retrieval) is acknowledged but unimplemented.
7. **Some of the data feeding the engine is dirty.** The recently-fixed parser bugs (commas, decimals, colons) silently scored correct answers as wrong. Whisper sometimes mishears (71 → 91). Those bad attempts now sit in the table polluting the diagnosis.

The plan that follows tackles these in priority order: visibility first (because the user already has a real diagnosis they can't see), session coherence second (because the scheduler's work is being diluted), grounding third (because that's the moat), then adaptation, retention, and data hygiene.

---

## 2. Principles this plan commits to

Each follows from [skill-progression-research.md](docs/skill-progression-research.md) or from the user's stated priorities.

| Principle | Source | What it forbids |
|---|---|---|
| **Mastery is two-dimensional: accuracy AND speed.** | Research §1.1 (Logan 1988; Anderson 1982) | Marking a slow, deliberate solver as "mastered." |
| **Component automaticity gates procedure.** | Research §1.2 (NRC 2001; Geary 2004) | Drilling 2-digit + 2-digit carry while single-digit sums to 20 are still slow. |
| **The problem-size effect is real and specific.** | Research §1.3 (Campbell 2005; LeFevre et al. 1996) | Lumping ×7 and ×8 in with ×2 and ×5. |
| **Spaced retrieval > massed practice.** | Research §1.4 (Cepeda et al. 2006) | Letting mastered facts vanish forever. |
| **State when there isn't enough data.** | User instruction | Showing a confident "your weakness is X" when X has 2 attempts and a noisy median. |
| **Relevance beats volume.** | [personal-cognitive-trainer-plan.md §10](docs/personal-cognitive-trainer-plan.md) | Adding more random problems to make sessions longer. Length should serve clustering, not volume. |
| **Adaptation must be felt.** | Same | Doing all the work in the backend and showing the user a generic table. |

The data-honesty principle is the one most easily violated. Every UI surface that names a fact as "your weakness" must also indicate the strength of that claim. The diagnosis engine's `min_attempts` and `n` fields exist for this; the UI must surface them.

---

## 3. Phased plan

Phases are sequenced by user-visible impact per unit of build. Each phase has a **success sign** that must be observable before moving on.

### Phase A — Make the diagnosis visible

**Why first:** The system already knows things the user doesn't see. Closing that gap is the highest leverage change possible — it requires no new modeling, only new surfaces.

**A1. Pre-session teaser.**
Before a drill starts, show the user what the session is about to focus on. Three lines, no more:

> Your slowest right now: **7×8** (8.2s, target 1.5s), **2d+2d with carry** (6.1s, target 4.5s).
> This session will drill those, with a few mastered facts mixed in to check retention.
> *Based on 47 attempts over the last 5 sessions.*

When `n` is low across the board, replace the third line with **"Diagnosis confidence: low — fewer than 10 attempts on most facts. Sessions will be exploratory until we have more data."** This is the data-honesty principle in practice.

**A2. Diagnosis-first review screen.**
The post-session review currently leads with an attempts table. Reorder to lead with diagnosis:

1. **What I noticed** — three bullets max. Two slow facts and one regression, or two slow facts and one mastered. Each bullet names the fact, current median latency, target, and `n`.
2. **What's next** — one sentence describing the focus of the next session.
3. **This session in detail** — the existing attempts table, collapsed by default.

Every claim names the evidence behind it. A regression bullet reads: "7×8 was 5.1s in your first 20 attempts; last 5 attempts averaged 8.2s." Not "you're getting worse at 7×8."

**A3. Coherent fact granularity on the profile page.**
The profile mixes `mul:6x7` (specific) with `add:2d+2d:c` (pattern). Two changes:

- **Multiplication: add a factor-family rollup.** Show "×7 facts overall: median 6.8s across 24 attempts" *and* the individual problem rows. The rollup is the diagnostic; the individual rows are the evidence.
- **Addition/subtraction: render pattern labels in plain English.** `add:2d+2d:c` becomes "Two-digit + two-digit, with carry." This is a renderer change, not an engine change — the underlying key stays.

A later phase (D2) will add representative slow *problems* under each addition pattern. Out of scope here.

**A4. Confidence indicators everywhere a fact is named.**
Every fact display gets a small `n=…` or a confidence chip (low / medium / high based on `n` thresholds — e.g., <5 low, 5–15 medium, >15 high). Cheap, but it's the structural fix for the data-honesty principle.

**Success sign:** The user can answer the question "what does this app think I'm bad at?" by pointing at a single screen, and they can tell from that screen whether the system has enough data to be making the claim.

---

### Phase B — Make sessions feel coherent

**Why second:** A1 promises the user the session will focus on specific weaknesses. Without B, A1 lies.

**B1. Within-session clustering.**
Today the scheduler picks each question independently. Replace with a session-level plan generated up-front:

- Pick 2–3 *theme facts* from the top of `drill_priorities`.
- Plan the session as: theme facts repeated 2–3 times each, interspersed with 1–2 related facts (same skill or same factor family) and 1–2 spaced-retrieval picks from mastered facts.
- For multiplication theme `7×8`, related could be `7×7`, `8×8`, `7×9`. For addition theme `add:2d+2d:c`, related could be other 2-digit-with-carry pairs of varying difficulty.

This is a small change to the scheduler: pre-compute a session plan in `pick_drill`'s session-init path; subsequent calls within the session pop from the plan instead of recomputing. Emits a clear event for the debug pane.

**B2. Default session length 12.**
5 is too short for B1 to build a theme. 12 lets the scheduler do 3 theme facts × 3 reps + 3 mixed-in related/retention. Make it user-configurable but default to 12.

**B3. Spaced-retrieval picks within a session.**
Each session reserves 1–2 slots for facts the user has mastered (accuracy ≥ 90%, median latency ≤ target × 1.1) but hasn't seen recently. This implements Research §1.4 in its simplest form: a recency-decayed sampler. Full SRS algorithms (FSRS, SM-2) are out of scope until we have data on what works for this user.

**B4. Regression weighting in priorities.**
[server/diagnosis.py:250](server/diagnosis.py:250) computes `drill_priorities` from gap and accuracy. Add a regression term: if a fact is in `recent_regressions`, multiply priority by 1.5. A fact that *was fast and is now slow* is a stronger signal than a fact that was always slow.

**Success sign:** Reviewing a session transcript end-to-end, the user can name the 2–3 themes the session was built around without being told.

---

### Phase C — Ground in real life: authentic sources first

**Why third:** The MVP plan calls this the moat. But grounding only works if the context is authentic. A generic arithmetic fact wrapped in fake personal framing is worse than an abstract drill because it feels manipulative. Grounded problems should come from real user data and should exercise arithmetic that naturally belongs to that source: calendar produces time arithmetic; expenses produce tips, tax, discounts, totals, category sums, budget deltas, and splits.

Calendar is still a strong first adapter because it is read-only, structured, and useful for time arithmetic. It should no longer be treated as the only near-term grounding path. A small expense ingestion path may be equally valuable if the user can provide data cheaply.

**C1. New skill: time arithmetic.**
Distinct from the existing four skills. New fact-key family: `time:duration`, `time:until`, `time:overlap`, `time:tz-shift`. New parameter schema (start, end, hours, minutes). New tolerance (probably "exact minutes" with `±1m` tolerance for spoken answers). This is a real piece of work — not a generator tweak — and goes through the same `skills.yaml` registration path as the others.

The research foundation: this skill is *out of scope* for the cited skill-progression literature, which focuses on abstract arithmetic. The principle of accuracy + median latency mastery still applies. Targets are starting guesses (probably 4–8s for "minutes between two times") that adapt per-user (Phase D).

**C2. Calendar adapter: read-only, scoped, cached.**
A `server/grounding/calendar.py` module:

- OAuth scope: `calendar.events.readonly`. Nothing more. Refused if the user objects.
- Pulls the next 7 days of events into a local cache (~50 events typical). Refresh on session start, not per-question.
- Stores nothing about the events except what's needed to generate problems: start, end, title (optional, for display only), all-day flag.
- Sensitive titles can be redacted via a user-configurable pattern list (e.g., "Therapy" → "Personal"). Default-on for all-day events that match common privacy-sensitive patterns.

The user must be able to disconnect at any time. When disconnected, time arithmetic falls back to synthetic problems generated from random clock times — still a valid skill, just not personal.

**C3. Expense adapter: user-supplied first, direct integrations later.**
Do not block grounding on a bank integration. Start with the lowest-friction private path:

- User drops in CSV exports from Chase Sapphire, Simplifi, or another finance tool.
- Optional manual seed file for recurring numbers: rent, subscriptions, commute time, usual meal costs, hourly rate, savings targets.
- Parser normalizes date, merchant, amount, category, currency, and source into a local cache.
- Raw merchant names are local-only; prompts can use merchant/category labels only when the user opts in.

Direct integrations can follow once the source is clear and the value is proven. The first implementation should not assume Chase, Simplifi, Plaid, or any other provider has the right API shape or permission model. CSV/manual import is enough to prove whether expense-grounded drills feel materially better.

Expense-grounded skills should be source-native, not contrived. Examples:

| Generator | Example | Inputs |
|---|---|---|
| `tip_total` | "Dinner was $63.42. What's an 18% tip, rounded to the nearest dollar?" | transaction amount, tip percent |
| `tax_total` | "A $48.00 item has 8.875% tax. Roughly what's the tax?" | amount, local tax rate |
| `category_sum` | "You spent $18, $27, and $34 on coffee this week. About how much total?" | grouped transactions |
| `split_cost` | "A $72 meal split 3 ways. About how much each?" | transaction amount, party size |
| `budget_delta` | "Your dining budget is $400. You've spent $265. How much is left?" | user seed or category budget |

**C4. Time-arithmetic generators.**

| Generator | Example | Inputs from calendar |
|---|---|---|
| `until_event` | "Your *Standup* starts at 10:30. It's 10:07. How many minutes until it starts?" | next event start time |
| `event_duration` | "Your *Design Review* runs 2:15 to 3:45. How long is it?" | event start, end |
| `back_to_back_gap` | "*Lunch* ends at 1:00. Your next meeting is at 1:20. How long is the gap?" | adjacent events |
| `leave_by` | "It's a 25-minute drive. Your *Dentist* is at 4:00. When do you need to leave?" | event start; user-supplied travel-time |
| `overlap_check` | "Your *1:1* is 2:00–2:30. Your *Planning* is 2:15–3:00. How many minutes overlap?" | two events |

**C5. No contrived personalization.**
The grounding layer must not take an arbitrary weak fact and invent a personal wrapper around it. For example, if `7×8` is slow, do not manufacture a fake "7 focus blocks of 8 minutes" prompt unless those numbers genuinely came from the user's data and the scenario is something the user would actually reason about. The generator should prefer plain abstract drills over fake relevance.

Grounded prompts must pass a source-native test:

- The numbers came from a real source or an explicit user seed.
- The operation is one the source naturally asks for.
- The prompt would still make sense if spoken outside the app.
- The app records which source produced the problem and whether the context was real, synthetic fallback, or user-seeded.

**Synthesis vs. lit:** none of the cited research speaks to "naturalistic time or expense arithmetic." This is design judgment, not science. The honest framing: we expect grounded problems to be more memorable and motivating, *not* automatically better for fluency transfer. We measure that in Phase F.

**C6. Privacy-conscious display.**
Problems show event titles, merchant names, or category labels only when the user opts in. The user can drill with redacted labels ("Meeting", "Restaurant", "Grocery") when screen-sharing or when the data is sensitive. Raw context stays local.

Skip mid-drill LLM feedback for now. The current LLM coaching path ([server/feedback.py](server/feedback.py)) can make the app feel like it is narrating instead of training, and grounded prompts would raise privacy questions if sent to an external model. Revisit LLM feedback later as a post-session or stubborn-pattern feature with explicit redaction and clear trigger rules.

**Success sign:** A drill session contains at least one problem the user instantly recognizes as drawn from their real week or finances, the prompt does not feel fabricated, and the privacy boundary is clear enough that they're not anxious about the integration.

---

### Phase D — Make targets and patterns adaptive

**Why fourth:** Phases A–C give the user something to feel. Phase D makes the system honest over the long run by letting the user's own data correct the starting guesses.

**D1. Per-user latency targets.**
Today: targets are constants from `skills.yaml`. Proposed:

- Each fact key has a `personal_target_ms`, defaulting to the skill-level constant.
- Once a user has ≥20 attempts on a fact key with ≥90% accuracy, recalibrate the personal target to `max(literature_floor, 0.8 × user_p50)`. The literature floor is the lower bound from Research §3 (≈0.8s for single-digit retrieval; higher for multi-digit).
- Targets only tighten, never loosen, until a regression is detected. A regression unfreezes the target back to the skill default plus a soft-recovery margin.

This implements Research §3 ("targets adapt per-user") explicitly. State the floor and the recalibration rule on the profile page so the user can see why a target moved.

**D2. Pattern-bucket splitting based on within-bucket variance.**
Research §2 explicitly anticipates this: "If the system shows that a particular stage is too coarse — e.g., if accuracy and latency on 'sums to 20' cluster differently for sums ≤14 versus sums >14 — that stage should split."

The current `add:2d+2d:c` covers `15+17` through `89+97`. Detect coarseness automatically:

- For each pattern bucket with ≥30 attempts, compute coefficient of variation of latency.
- If CV > 0.6 (chosen by inspection of real data — currently a guess), surface a "this bucket is heterogeneous" hint on the profile page and add a sub-key dimension (e.g., sum range: ≤50 / 51–100 / 101–198).
- Auto-splitting the bucket in the diagnosis engine is the next step but should not be done blindly. Make the split *visible* before making it *operational*.

**D3. Multiplication mid-tier (×3 through ×9 minus ×2/×5/×10).**
The current generator's L1 covers ×2/×5/×10; L2 jumps straight to ×6–×12. Per Research §1.3, the ×7 and ×8 facts are the canonical retrieval bottleneck and deserve their own focus tier. Add an L1.5 that drills 3×3 through 9×9 *minus* the ×2/×5/×10 family. This is generator-only work; the diagnosis engine already keys by specific fact.

**Success sign:** After a month of use, at least one skill's personal target differs measurably from the skill default, and the user can see why on the profile page.

---

### Phase E — Retention

**Why fifth:** Phase B introduces lightweight spaced retrieval. Phase E commits to it as a first-class layer.

**E1. Retention queue.**
A fact is considered *mastered* when accuracy ≥ 90% and median latency ≤ personal target on its last 10 attempts. Mastered facts enter a retention queue with a recency-based interval schedule:

- First retention check: 2 days after mastery.
- Successful retention check: double the interval (capped at 30 days).
- Failed retention check: drop the fact back to active drilling and reset the interval.

This is Leitner-style, deliberately simpler than SM-2/FSRS. The research doc explicitly suggests starting simple and refining later.

**E2. Decay detection.**
A fact whose retention check median latency creeps up by 30%+ over 3 consecutive checks gets flagged as "decaying" — surfaced on the profile distinct from "regressed" (which is short-term).

**E3. Session mix.**
Phase B's spaced-retrieval slots draw from the retention queue rather than just "any mastered fact not seen recently." Sessions allocate 70/20/10: weakness drilling / related-fact mixing / retention checks.

**Success sign:** The user notices a previously mastered fact coming back weeks later, and either passes it (reinforcing automaticity) or fails it (catching real decay before it becomes a regression).

---

### Phase F — Data hygiene and measurement honesty

**Why ongoing, scheduled here:** these issues exist today, but solving them before A–E inverts priorities. Some can be done concurrently with A.

**F1. One-time cleanup of pre-fix bad attempts.**
The parser bugs (commas, decimals, colons) silently scored correct answers as wrong before [49b15fd](.). Two options:

- Hard delete attempts where parsed_answer is suspect (e.g., parsed=1 against expected=1078, an order of magnitude off). Cleaner data, but loses signal on real wrong answers.
- Soft flag with a `quarantined` column and exclude from diagnosis. Preserves the row for audit. Recommended.

**F2. STT-mishearing recovery.**
Add a "that's not what I said" button on the result screen. Pressing it reverses the attempt: re-record the answer without recording a wrong attempt. Track the rate of reversals — if >5% of attempts, the cost of Whisper noise is worth a constrained-vocabulary STT prompt or an alternate input modality.

**F3. Sanity filter at write time.**
Reject (or quarantine) attempts where parsed_answer differs from expected by more than 99% AND the raw transcript contains digits matching the expected answer. This catches "1,078 → parsed as 1" patterns going forward.

**F4. Session-end "did anything go wrong?" prompt.**
After a session, a one-line opt-in: "Was anything in this session graded incorrectly? Click to flag." Cheaper than a per-attempt button and lower friction.

**Success sign:** Fewer than 1% of attempts are quarantined per session, and the user trusts that the diagnosis is built on real data.

---

## 4. What this plan deliberately does NOT do

To keep scope honest:

- **No mobile.** [personal-cognitive-trainer-plan.md](docs/personal-cognitive-trainer-plan.md) defers it; Phase 0 is Mac-only. The debug panel collapse (todo #23) is a 30-minute fix and can land any time, but it's a UX nicety, not a phase.
- **No multi-device sync.** SQLite stays local. The data-export piece (todo #25) is a single endpoint when needed; do not pre-build sync.
- **No bank-first integration.** Expense grounding may start soon, but through CSV/manual import first. Direct Chase/Simplifi/Plaid-style integration comes only after source-native expense drills prove useful.
- **No mid-drill LLM coaching.** Conversational AI is explicitly out of scope per the MVP doc. Any future feedback work should be post-session or stubborn-pattern only, and should have explicit privacy/redaction rules.
- **No Bayesian Knowledge Tracing or full SRS.** The research doc names BKT explicitly as a model the system *does not* import wholesale. Phase E uses Leitner because it's simpler, easier to explain, and adequate for the data volumes a single user will produce.
- **No new cognitive domains** (people-memory, navigation, episodic recall). Phase 4 in the MVP plan; not now.

---

## 5. Open questions where the plan needs more data before deciding

These are the places the plan punts honestly rather than guessing:

| Question | Why it's open | What would resolve it |
|---|---|---|
| Is 12 the right session length? | Chosen as 2.5× current, not measured. | Track session-completion rate vs. length. If users skip out at 8, drop to 10. |
| Is the 2-3-2 theme/related/retention mix right? | Inspection-only design choice. | Compare same-session-as-prior-day retention rates across mixes. Probably needs A/B and we're n=1. |
| Is `mul:7x8` distinct enough from `mul:8x7` to bother? | The diagnosis engine treats them as one (sorted). Some research suggests order-of-presentation matters for retrieval. | Look at within-key latency variance after presentation order is logged. Currently not logged. |
| Should the regression weight be 1.5×, 2×, or scaled by ratio? | Pulled from intuition. | Run with 1.5× for two weeks, see how often regressions then dominate the priority list. Adjust. |
| Does naturalistic grounding (calendar or expense problems) actually transfer to abstract fluency? | The research doc is explicit: not addressed by the cited literature. | Compare median latency on equivalent abstract problems before vs. after a month of grounded drilling. n=1 limits causal claims; track the data anyway. |
| Which expense source is worth supporting first? | Chase Sapphire and Simplifi may be useful, but provider APIs/exports/permissions need verification before implementation. | Start with CSV/manual import. If usage sticks, evaluate the most reliable direct connector. |
| What's the right CV threshold (D2) for splitting a bucket? | 0.6 is a guess. | Plot CV histograms across all current buckets; pick the threshold that's an obvious gap. |
| What's the floor for tightened personal targets (D1)? | "Literature floor" is fuzzy — single studies report different values. | Pick a conservative floor (1.0s for single-digit, 3.0s for 2d+2d-no-carry, 4.5s for 2d+2d-carry) and revisit when 1000+ attempts exist. |

When the system lacks data for any of these, the corresponding UI surfaces should say so, not invent confidence.

---

## 6. Sequencing summary

| Phase | Headline change | Time-to-value | Hard prerequisites |
|---|---|---|---|
| **A** | Diagnosis becomes visible (teaser, review, profile coherence, confidence chips) | High, immediate | None |
| **B** | Sessions feel coherent (clustering, length 12, retention slots, regression weight) | High | A (so the user can verify) |
| **C** | Authentic grounding: calendar time math plus CSV/manual expense grounding | Medium-high; this is the "stickiness" wedge | None technically, but A+B make the new skills assessable |
| **D** | Adaptive targets, bucket splitting, mul mid-tier | Medium; long tail of trust | A's confidence chips; one month of A+B+C data |
| **E** | Retention queue and decay detection | Compounds over months | D's adaptive targets define mastery |
| **F** | Data hygiene (quarantine, STT recovery, sanity filter) | Defensive; ongoing | Land F1 alongside A. The rest is concurrent. |

Each phase ends on its **success sign**, not on its task checklist. If A's success sign isn't real, B doesn't start.

---

## 7. What this plan is and is not

This plan is opinionated about sequence and about which research principles bind which decisions. It is not opinionated about implementation details — those belong in the per-phase design docs that get written when each phase starts.

It commits to one thing: **the user should never be told the system has a strong opinion when the underlying data is weak.** Every phase has UI surfaces that make claim-strength visible. That's the difference between a trainer and a toy.
