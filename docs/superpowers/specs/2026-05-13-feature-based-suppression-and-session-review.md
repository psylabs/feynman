# Feature-Based Suppression + Session Review

**Date:** 2026-05-13

## Problem

1. Suppression rules have per-skill `if skill_id != ...` guards. Every new skill (financial math, health math) requires editing rule functions — LoC grows with skills.
2. User feedback flagged problems that should be suppressed but weren't: subtrahend ≤ 2, subtract 10, weather delta of 1.
3. Session review makes claims on n=2 ("still weak"), shows no within-session stratification, and has no cross-session pattern detection.

## Shared Foundation: Feature Tagging

Generators compute a `features` sub-dict inside `parameters`:

```json
{ "a": 17, "b": 2, "borrow": true, "level": 2,
  "features": { "abs_diff": 15, "min_operand": 2, "max_operand": 17 } }
```

| Field | Definition |
|---|---|
| `abs_diff` | `\|a − b\|` for arithmetic; `\|stronger − calmer\|` for weather deltas |
| `min_operand` | smaller of the two key operands |
| `max_operand` | larger of the two key operands |

Generators with no meaningful operand pair (e.g., `f_to_c_approx`) emit `features: {}`. No schema change — features live inside the existing `parameters` JSON column.

A shared helper `_compute_features(a, b) -> dict` is used by all additive generators. Weather delta generators compute their own equivalent.

## Part 1: Suppression Rules Refactor

### Rule changes

All `skill_id` guards removed. Rules check `params.get("features", {})` only. A skill that doesn't compute a feature gets `False` safely.

| Rule | Old logic | New logic |
|---|---|---|
| `single_digit_small` | both a,b ≤ 2 | `max_operand ≤ 2` |
| `trivial_diff` | `\|a−b\| ≤ 2` (subtraction only) | `abs_diff ≤ 2` |
| `round_diff` | `\|a−b\| in set` (subtraction only) | `abs_diff in set` |
| `subtract_zero` | `b==0` (subtraction only) | `min_operand == 0` |
| `equal_operands` | `a==b` | `min_operand == max_operand` |
| `by_ten` | `a==10 or b==10` (mult only) | `10 in (min_operand, max_operand)` |
| `small_operand` *(new)* | — | `min_operand ≤ 2` |

### suppressions.yaml additions

```yaml
addition:
  - single_digit_small
  - small_operand
  - by_ten

subtraction:
  - ...existing...
  - small_operand   # covers b ≤ 2 feedback
  - by_ten          # covers "subtract 10" feedback

weather_math:
  - trivial_diff    # delta ≤ 2
  - round_diff      # delta in {5, 10, 50, ...}
```

### Extension path

Adding a new skill = compute features in the generator. Zero changes to `suppressions.py`.

## Part 2: Historical Migration

One-time script: walk all existing attempts, compute features from stored `parameters` using the same helper, write back to `parameters` JSON. ~153 rows. Run once after deploy.

## Part 3: Pattern Detection Engine

New `pattern_analysis(attempts: list[dict]) -> list[dict]` in `session_analysis.py`. The caller pre-fetches all correct attempts for the user (with `resolution_latency_ms` non-null) and passes them in.

1. Compute `skill_baseline`: median latency per `skill_id` across all passed attempts
2. For each feature key/value pair found in `parameters["features"]`, group attempts and compute `(n, group_median)`
3. Filter: `n ≥ 5` and `ratio = group_median / skill_baseline > 1.4`
4. Return top 3 by ratio descending

Output shape: `[{ skill_id, feature_key, feature_value, ratio, n, group_median_ms, baseline_ms }]`

## Part 4: Session Review Restructure

Three levels replace the current flat view:

**Level 1 — Top-line** (unchanged): accuracy + latency per role (theme, related, retention, exploration).

**Level 2 — Within-session stratification** (new): for each focus skill, split attempts by a skill-specific categorical param — `borrow` (true/false) for subtraction, `operation` (wind_delta/temp_delta/etc.) for weather_math. Only rendered when both buckets have ≥ 2 attempts. These params already exist on every attempt; no new data needed.

**Level 3 — Cross-session patterns** (new): output of `pattern_analysis`, shown as 2–3 lines at the bottom of the review. Omitted entirely when no group clears n ≥ 5. Format: *"Subtraction with borrow: 3.1× slower than your baseline (n=42, 2.1s vs 0.65s)"*

**`still_weak` fix**: any session-level mastery or weakness claim requires n ≥ 5 attempts in the session. Drop the line entirely when below threshold instead of making a low-confidence claim.

## Part 5: UX Fix

Add `keydown` handler on `.fb-reason` inputs: Enter key triggers the same submit flow as clicking the save button. Prevents duplicate submissions when user presses Enter expecting submission.

## Out of Scope

- Multiplication heatmap (separate feature)
- `crosses_zero` and other structural features (added when subtraction subtype work lands)
- Statistical trend detection (improving/regressing) — needs more history
