# TODO

Current product concern: the curriculum was just re-prioritized to a foundation lane (0-30 add/sub/mul/div) plus a 50/50 grounded lane (weather + money), see [curriculum-redesign.md](curriculum-redesign.md). The diagnostic-feature work below (feature tags like `sub.borrow.across_zero`) is **deferred until the foundation lane has produced clean data**. Bucketed labels like "three-digit minus two-digit" are no longer a live concern because the generators don't produce 3-digit operands anymore.

## Tier 1 — Make Weaknesses Specific Enough To Be Believable (deferred)

> Deferred: re-evaluate after a few weeks on the new foundation lane to see whether the existing fact-key granularity (`mul:7x8`, `add:2d+2d:c`, `div:7x8`) is already specific enough now that the operand ranges are tight. If gaps remain, then add the feature tags below.

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

## Tier 2 — Game Mechanics / Engagement

1. **Fluency score on home screen.** One number — median correct-answer latency over the last 14 days across all practiced facts. Lower is better (golf score). Show a week-over-week delta (↓ 0.3s). This is the unambiguous "am I getting better" signal.
2. **Streak tracking — both dimensions.** Show (a) current consecutive-day streak and (b) total days ever practiced. Both visible on home screen. A broken streak should feel like a loss.
3. **Shorter default session.** Default drill length → 5q ("quick drill"). Make that the primary CTA on the home screen. Longer options (8, 12, 20) remain available but secondary. Goal: sub-3-minute default session so activation energy is near zero.
4. **Mastery graduation events.** When a fact's median latency crosses a "unreasonably good" threshold (to be defined by user after seeing real data — something like sub-1.5s for mul), show a brief "Mastered: 7×8" moment at session end with a running total of mastered facts. Defer until there's enough data to calibrate the threshold.

## Tier 4 — Curriculum and Problem Generation

see curriculum-redesign.md

## Tier 5 — Data Pipeline and Adaptation

1. **Per-user adaptive latency targets.** The skill-wide targets are starting guesses. Once a user consistently beats a target, it should tighten, and the profile should show why.
2. **Retention schedule needs to mature.** Sessions now include simple retention picks, but the interval logic is still basic. Mastered facts should resurface at growing intervals and flag decay when latency creeps up.

## Tier 6 — Grounded/Real-Life Problems

1. **Pull from calendar for time-math problems.** Calendar can be one authentic grounding source, but it should not become the whole grounding strategy.
2. ~~Add CSV/manual expense grounding before direct finance integrations.~~ Done — `data/transactions.csv` drives `money_arithmetic` (charge_total, category_difference, category_share).
3. ~~Expense-grounded generators should be source-native.~~ Done as part of #2.
4. **Context requires new skills.** Time arithmetic and unit conversion still need their own generators, tolerances, and fact keys. Money + weather are now wired up; calendar/time arithmetic is the next grounded surface to add.

