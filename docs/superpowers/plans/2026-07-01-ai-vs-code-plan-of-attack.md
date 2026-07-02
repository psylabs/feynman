# Plan of Attack: Queue Stickiness + Feedback (AI-vs-Code Pass)

Research pass, 2026-07-01. Grounded in the live DB (409 attempts, 117 sessions,
17 active days, 2026-05-02 → 2026-06-26) and a full read of the scheduling,
suppression, generation, and grading code. Mechanism classes: **(1)** deterministic
Python/SQL, **(2)** LLM runtime/batch, **(3)** managed agent / persistent memory.

## 1. What the data says

**Queue mix is broken, and that — not suppression — is the root complaint.**
- subtraction = 206/409 attempts (50%); money 75; addition 54; weather 45;
  multiplication 15 (none since May 13); percent_of 14; **division 0 ever**.
- Subtraction is stuck at L2 since May 7: 163 attempts, 94% acc, ~3.3s. L3 is the
  actual frontier (15 attempts, 53% acc) and barely gets sampled.
- Recent sessions are all "3-4 subtraction + 2 money". 409 attempts / 279 distinct
  prompts; "What is 15 minus 12?" shown 8 times across 7 weeks.

**Real weaknesses (worst first, min n=8, skips excluded):**

| subtype | n | acc | ms (when right) | read |
|---|---|---|---|---|
| money/category_share | 7 | 0.00 | — (6 skips, 16s) | broken problem design |
| weather/f_to_c_approx | 12 | 0.50 | 8981 | weakest real skill |
| money/category_difference | 11 | 0.45 | 3065 | inaccurate |
| subtraction L3 | 15 | 0.53 | 5532 | the actual frontier |
| money/split_bill | 14 | 0.71 | 6171 | slow + inaccurate |
| addition L2 (carry) | 46 | 0.87 | 4038 | slow for a "primitive+" |
| money/restaurant_tip_15 | 19 | 0.95 | 6365 | accurate but slow — speed drill |
| multiplication | 15 | 0.73 | — | 9×10, 9×11, 12×9 missed; n too small to map gaps |

**Feedback is 100% queue-relevance signal, not learning signal.** 93 rows,
28 thumbs-down; every reason is "Too simple" / "Too frequent" / "I was just asked
this" (one explicitly: "There's merit to asking when I got it wrong"). Flagged
prompts recurred after the thumbs-down (16−12 shown 2× after, 14−7 2×, 14−11 2×).

**Data integrity smells:** skill_state says multiplication attempt_count=27 but the
attempts table has 15 rows — skill_state and live diagnosis are two disagreeing
sources of truth. `resolution_latency_ms` includes speaking + STT time;
`onset_latency_ms` (time-to-start-speaking) is the cleaner automaticity signal and
is already captured.

## 2. Root-cause model (three mechanisms explain all Group A symptoms)

1. **Feedback is a dead end.** `user_feedback` is written (storage.py:395) and
   *never read* — no SELECT anywhere in the codebase. Suppression is a separate,
   hand-edited YAML → Python predicate registry (suppressions.py), loaded once and
   cached until process restart (suppressions.py:96-115). After 20 failed
   re-samples the generator returns the suppressed problem anyway, silently
   (generator.py:344). So "suppression isn't firing" = feedback never becomes
   rules + existing rules don't cover the flagged cases (16−12 has abs_diff=4;
   `trivial_diff` won't match) + no telemetry to see any of this.
2. **The scheduler is a closed loop over past attempts.** Priorities come only from
   fact-stats over the last 300 attempts (scheduler.py:112-123). Zero-history
   skills (division) can never enter; skills that age out of the window
   (multiplication) never return; difficulty comes from static tier tables
   (`_infer_level_from_key`), so subtraction never advances past L2 despite 94%
   accuracy. **Most "too simple" feedback is this bug, not a suppression gap.**
3. **FSRS does not exist.** Zero hits for fsrs/stability/retrievability/due in the
   repo. "Retention" = rank mastered facts by days-since-seen, capped at 0-2 slots
   per session. `mastery.py`'s scalar is display-only — the scheduler never reads
   skill_state.

## 3. Per-item verdicts

### B1. Taxonomy — the crux, and it's (1) deterministic
A taxonomy already half-exists: `diagnosis.fact_key` produces `mul:6x7` (specific
fact), `add:2d+2d:c` / `sub:2d-1d:b` (pattern buckets), `money:split_bill`
(operation). The grain is inconsistent: mul/div are item-level, add/sub throw away
operands, grounded is op-level. The stored `parameters` JSON (a, b, carry, borrow,
features) is rich enough to re-key everything — no schema change, no new capture.

**Proposal — two tiers, one enum:**
- **Primitives** (finite, enumerable, instant-recall target on *onset* latency
  ~1.5-2s): add/sub facts within 20 as specific commutative-normalized keys
  (`add:7+8`), times/division tables incl. ×12/15/20, percent anchors
  (`pct:15of10x`), the F→C rule as a single learned procedure-fact.
- **Compound** (procedure classes, feature-tagged, judged on resolution latency
  vs target): multi-digit carry/borrow patterns, money/weather operations, future
  unit conversions. Each compound type carries a static list of component
  primitives (tip_15 → pct:10, halving, rounding) — for *diagnosis display only*
  in v1; no cross-crediting into scheduling yet.

**AI-vs-code call: confirm the prior Opus conclusion — no AI.** (I could not find
that analysis in the repo; evaluated independently.) The type space is small,
enumerable, and derived from generator structure you control; classification is a
pure function over already-structured params, backfillable over all 409 attempts in
one script. The counter-case boundary: an LLM classifier becomes warranted only if
problems ever become free-form generated text without structured params. Where an
LLM legitimately helps is *this design conversation* — proposing where the
primitive/compound line sits — a one-time offline judgment, not a runtime mechanism.

**Effort:** S-M. `fact_key_v2()` + backfill script + component map. Highest
leverage-to-code ratio in the whole plan. **Depends on:** nothing.

### A2. Anti-forgetting / FSRS — (1) deterministic
FSRS is greenfield. Use `py-fsrs` (pure Python, deterministic, default params —
409 attempts is far too little to fit personal params).
- **Item = B1 unit**: primitives are true FSRS items (memory retrieval); compound
  classes get FSRS too but treat it as "skill freshness" with a coarser grade.
- **Grade mapping is free from existing data**: wrong → Again; correct but over
  latency target → Hard; correct within target → Good; correct + fast + (new)
  "too easy" thumb → Easy. No new capture needed.
- **Scheduling**: due items claim slots *first*, then weakness priorities, then
  novelty (below). This replaces the 0-2 retention quota and the 300-attempt
  window (FSRS state persists, so multiplication can't silently age out again).
- **Bootstrap fix**: any skill/primitive with zero attempts gets a small
  "assessment" weight so division actually enters rotation.
- **Offline constraint to accept**: seed packs bake the due-queue at build time;
  staleness of hours is fine for a single user.

**Effort:** M. New `item_state` table + grade hook + scheduler rewrite of the
retention/priority merge. **Depends on:** B1.

### A1. Suppression — (1) deterministic; redesign the model
Coherent model: **feedback is a scheduler signal, not a parallel rule system.**
- "Too easy" thumb → grade that item Easy in FSRS (long interval) and raise its
  level floor. This is exactly what your own feedback text asks for.
- "Too repetitive" → recency penalty bug report; fix via novelty term (A3).
- Keep a *tiny* generation-time floor registry for degenerate problems (×1, ×10,
  subtract-zero, equal operands, percent-of-100) — but it should shrink, not grow.
- Plumbing fixes regardless of taxonomy: read the damn table; structured reason
  chips (too easy / too hard / repetitive / bad problem) + optional free text;
  reload suppressions on change; log when the 20-retry give-up path fires.

**AI-vs-code call: no LLM.** 93 feedback rows are stereotyped; chips fix parsing
forward; batch-LLM categorization of free text is solving a problem the UI should
prevent. **Effort:** S (plumbing) + S (semantics once A2 exists).
**Depends on:** plumbing independent; semantics on B1+A2.

### A3. Variety & relevance — (1) deterministic; optional (2) batch later
Principle: **separate skeleton from skin.** The *skeleton* (which fact/class to
practice) comes from FSRS-due + weakness priority — that's the learning signal.
The *skin* (merchant, weather, framing) is where variety lives, and randomizing it
costs nothing pedagogically. Concretely:
- Replace reserved slots (50% grounded quota, 2 hardcoded tip/split staples) with
  weighted sampling: `weight = due_urgency + weakness + novelty_penalty(recent
  same-fact, same-prompt, same-merchant)`. Keep a soft grounded-share target
  (configurable, not `length // 2` hardcoded).
- Grounded generators become skins over skeleton requests where they can (a tip
  problem *is* pct:15 + rounding practice — schedule it as such).
- Keep the Plaid batch exactly as is — it's the best part of the pipeline.
- Kill or redesign `category_share` (0/7, 6 skips, 16s — it's a mental-division
  problem with ugly numbers, not a skill you're training).

**Optional (2), later:** a nightly batch LLM that drafts *new grounded templates*
from Plaid/calendar data, deterministically validated (numbers must compute, answer
exact) before entering the pool. This is the one defensible generation-LLM: batch,
verifiable, low frequency. Not needed until template variety actually feels stale
after the novelty fix. **Effort:** M. **Depends on:** A2 (weights need due/weakness
terms); the staple-removal + novelty penalty parts are independent.

### A4. LLM-as-judge on generated problems — **No.** 
Problems are template-generated; arithmetic correctness is guaranteed by
construction. Every observed quality failure (category_share, f_to_c difficulty,
"too simple") is *type-level, not instance-level*, and each was visible in plain
SQL (skip rate, accuracy, latency, thumb rate). A runtime judge adds latency and
cost to catch a failure mode that doesn't occur at the instance level.
**Narrowest useful version:** a weekly *type autopsy* — pure SQL flags subtypes
with outlier skip/accuracy/latency/thumb rates; optionally one batch LLM call to
draft the prose summary (or a follow-on Claude session; a cron job is not
justified yet). Revisit only if generation ever becomes LLM-authored.
**Effort:** S. **Depends on:** B1 for good grouping.

### B2. Strategy feedback — (1) templated code, LLM only as offline author
This is the app's point, and it must never be arithmetically wrong — which is the
argument against runtime generation. Build a **strategy library**: named
strategies, each = (applicability predicate over operands, templated rendering
with computed numbers). Examples: round-and-adjust (operand within 2 of a round
number), complement-to-10 pairs, difference-of-squares (51×49 → 50²−1), tip =
10% + half, F→C = subtract 30, halve. Detection and number-filling are pure
Python, so every tip is verifiably correct. Delivery: post-session review, attached
to wrong or slow-correct attempts (mid-drill coaching stays off — that decision was
right). Failure-mode split is already derivable: wrong (correct=0, use
error_magnitude) vs slow-correct (correct=1, latency > target).
- **LLM roles that ARE justified:** (a) authoring the library offline —
  human-reviewed once, durable value, zero runtime cost; (b) *optional* fallback
  for attempts no predicate matches, reusing the already-built `feedback.py`
  (gpt-4o-mini, post-session batch, clearly labeled) — cut it if tips are ever
  wrong.
**Effort:** M (the library is the work; ~10 strategies covers your observed
weaknesses). **Depends on:** B1 (strategies attach to subtypes).

### B3. Agent memory — **No.**
The attempts table is already a perfect memory: structured, verifiable, queryable,
and FSRS state (A2) is its distilled form. Managed-agent memory would add a fuzzy
natural-language shadow copy ("user struggles with borrowing") that drifts, can't
be audited against data, and duplicates the source of truth — pure maintenance
cost. Nothing it uniquely captures (affect? fatigue?) is actionable in this app.
The legitimate NL artifact is a *regenerable* coach report (a view over the data,
recomputed on demand), which is B2/A4 output — not memory. **Class (3) rejected.**

### B4. Feedback UI — flagged only
Dependencies confirmed: B1 (per-primitive mastery display — "next level" needs
levels), A2 (due/streak semantics). Also Tier-2 todo items (fluency golf score)
become computable per-primitive after B1. Nothing else to decide now.

## 4. Sequence of attack

**Shared foundation is B1 + A2 together** — the taxonomy plus persistent per-item
state. Your dependency hypothesis (B1 → A1, A2, B2, B4) is **confirmed with one
correction**: A1's *plumbing* (dead-end table, stale cache, silent give-up, reason
chips) is taxonomy-independent and can ship first; only A1's *semantics*
(feedback → FSRS grade) needs B1+A2. And the biggest lever on Group A is not
suppression at all — it's breaking the scheduler's closed loop (root causes 2+3).

0. **Hygiene (independent, ~1 day):** suppression reload + give-up telemetry;
   feedback reason chips; retire STT skip-probe (doubles STT cost, experiment
   done); disable category_share; resolve skill_state vs attempts count mismatch
   (pick live-diagnosis as truth, demote skill_state to display or delete).
1. **B1 fact_key v2** + backfill + primitive/compound map + component edges.
2. **A2 FSRS**: item_state table, grade hook, due-first scheduling, zero-history
   bootstrap, level progression from evidence instead of static tables.
   *(Kills most "too simple" complaints on its own.)*
3. **A1 semantics**: thumb → grade/floor mapping. Shrink suppressions.yaml.
4. **A3**: weighted sampling replaces reserved slots + novelty penalty; grounded
   skins over skeleton requests.
5. **B2 strategy library** (start authoring during 1-4; it's parallelizable).
6. **A4 type-autopsy SQL report** (anytime after 1); B4 UI last.

## 5. Framing corrections & open flags

- **"Suppression isn't firing" was three bugs**, and the fix is mostly *not* more
  suppression — it's scheduling. Expect suppressions.yaml to shrink.
- **Your n is tiny where it matters most**: multiplication 15 attempts, division 0.
  Don't trust the ×9/×12 gap map yet; step 2's bootstrap will generate the data.
  FSRS on defaults is correct at this scale; don't fit parameters.
- **Latency metric decision needed**: primitives should be judged on
  `onset_latency_ms` (already captured), not `resolution_latency_ms` (includes
  speaking + STT round-trip). Thresholds differ accordingly.
- **f_to_c_approx at 38-50%** despite the rule being stated in the prompt is worth
  a manual look — it may be a prompt/parsing problem (negative temps? rounding
  direction?), not a skill gap.
- **Total AI footprint after this plan**: STT + TTS (unchanged), optional
  post-session tip fallback, optional weekly autopsy prose, optional future batch
  template author. Zero new runtime LLM calls on the answer path. No agents, no
  agent memory.
