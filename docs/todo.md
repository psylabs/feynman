# TODO

Current product concern: the app is starting to show session intent and post-session fluency feedback, but the diagnosis vocabulary is still too coarse. A label like "three-digit minus two-digit, with borrow" is a generator bucket, not a satisfying weakness explanation. The next product step is to figure out the right diagnostic level before adding more review polish.

## Tier 1 — Make Weaknesses Specific Enough To Be Believable

1. **Design diagnostic features instead of relying on broad skill buckets.** Addition/subtraction/percent attempts need interpretable feature tags that explain *why* a problem was hard, not just which generator bucket produced it. Examples to evaluate:
  - `sub.borrow.ones`
  - `sub.borrow.across_zero`
  - `sub.crosses_hundred`
  - `sub.subtrahend_high_ones`
  - `sub.subtrahend_ends_5`
  - `add.carry.ones`
  - `add.carry.tens`
  - `pct.unfriendly_base`
  - `pct.requires_fraction_decomposition`
   The review should be able to say: "Today's slow correct answers clustered around ones-place borrowing and crossing a hundred boundary," rather than "you are weak at 3-digit minus 2-digit subtraction."
2. **Figure out claim strength for feature-level diagnostics.** The app should distinguish:
  - session evidence: "today's slow attempts had this feature"
  - provisional pattern: "this has appeared across a few sessions"
  - stable weakness: "this is now a reliable weakness record"
   Do not present feature tags as stable weaknesses until the evidence supports it.
3. **Make diagnosis records trace back to attempts.** Any review statement should be explainable from stored attempt data: skill, parameters, latency, correctness, derived fact key, derived feature tags, target latency, and sample size. Avoid adding a parallel "feedback" island that cannot be reconciled with the weakness/skill-state records.
4. **Decide whether feature tags become scheduler targets.** First make feature-level diagnosis visible. Only then decide whether the scheduler should generate targeted probes like "subtraction with borrow across zero" or whether it should keep drilling concrete problems and merely use feature tags for analysis.

## Tier 4 — Curriculum and Problem Generation

1. **Multiplication: add 3x3 through 9x9 as a distinct L1.5 tier.** Currently L1 is only x2, x5, x10 and L2 jumps straight to x6-x12. The hard table facts should be drillable as a focused set, not mixed in with x11 and x12.
2. **Addition/subtraction buckets are probably too coarse.** This is now part of the diagnostic-feature work, not just a label-cleanup task. `2d+2d:c` and `3d-2d:b` cover too many different mental routines.
3. **Percent drills need more variety.** The percentage pool is small, and real-life tax/tip/discount problems include less friendly rates and bases.
4. **Future feedback design: if/how to improve coaching.** Keep mid-drill LLM feedback out of scope. If feedback returns, it should be post-session or stubborn-pattern only, grounded in stored diagnostic evidence with explicit privacy/redaction rules.

## Tier 5 — Data Pipeline and Adaptation

1. **Per-user adaptive latency targets.** The skill-wide targets are starting guesses. Once a user consistently beats a target, it should tighten, and the profile should show why.
2. **Retention schedule needs to mature.** Sessions now include simple retention picks, but the interval logic is still basic. Mastered facts should resurface at growing intervals and flag decay when latency creeps up.

## Tier 6 — Grounded/Real-Life Problems

1. **Pull from calendar for time-math problems.** Calendar can be one authentic grounding source, but it should not become the whole grounding strategy.
2. **Add CSV/manual expense grounding before direct finance integrations.** Let the user provide Chase Sapphire, Simplifi, or other finance exports as local CSV files; also support a manually maintained recurring-numbers file for rent, subscriptions, commute time, usual meal costs, budgets, and savings targets.
3. **Expense-grounded generators should be source-native.** Use real transaction/category/budget data for tip, tax, discount, split, total, and budget-delta drills. Do not wrap arbitrary weak facts in fake personal scenarios.
4. **Context requires new skills.** Time arithmetic, money arithmetic, unit conversion, and similar grounded skills need their own generators, tolerances, fact keys, and diagnostic features.

## Tier 7 — Infrastructure

1. **Debug panel takes up half the screen.** Make it collapsible or hidden by default.
2. **Mobile/tablet support.** The current grid layout assumes a wide screen with a debug pane.
3. **Data export.** The SQLite DB is the only record. A JSON export of attempts would be useful for analysis outside the app.
4. **Multiple devices / sync.** Currently localhost-only with a local SQLite file. If practice happens from multiple machines, data does not follow.

