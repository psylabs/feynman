# Feature-Based Suppression + Session Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tag every generated problem with normalized features, refactor suppression rules to be skill-agnostic, and add two new session review layers — within-session stratification and cross-session pattern detection.

**Architecture:** Generators compute a `features` sub-dict in `parameters`; suppression rules check `features` only (no `skill_id` guards). `session_analysis.py` gains `pattern_analysis()` and `_session_stratification()`; the orchestrator wires cross-session patterns into the session-end response. A one-time migration script backfills existing 153 attempts.

**Tech Stack:** Python 3.14, SQLite (via `storage.py`), vanilla JS (`web/app.js`). Test runner: `.venv/bin/python -m unittest discover tests -v`

---

### Task 1: Feature tagging in generators

**Files:**
- Modify: `server/generator.py`
- Modify: `server/weather.py`
- Create: `tests/test_suppressions.py`

- [ ] **Write failing test** — generator output includes features

```python
# tests/test_suppressions.py
import unittest
from server import generator

class FeatureTaggingTests(unittest.TestCase):
    def test_subtraction_params_include_features(self):
        result = generator.generate("subtraction")
        f = result["parameters"]["features"]
        self.assertIn("abs_diff", f)
        self.assertIn("min_operand", f)
        self.assertIn("max_operand", f)
        self.assertIn("has_borrow", f)
        a, b = result["parameters"]["a"], result["parameters"]["b"]
        self.assertEqual(f["abs_diff"], abs(a - b))
        self.assertEqual(f["min_operand"], min(a, b))
        self.assertEqual(f["max_operand"], max(a, b))

    def test_addition_params_include_features(self):
        result = generator.generate("addition")
        f = result["parameters"]["features"]
        self.assertIn("has_carry", f)

    def test_weather_delta_includes_features(self):
        # weather may hit network; use the fallback by forcing a known op
        from server import weather
        loc = {"name": "Test", "lat": 0.0, "lon": 0.0}
        forecast = weather._fallback_forecast()
        result = weather._wind_delta(loc, forecast)
        f = result["parameters"]["features"]
        self.assertIn("abs_diff", f)
        self.assertEqual(f["abs_diff"], abs(result["parameters"]["stronger"] - result["parameters"]["calmer"]))
        self.assertEqual(f["operation"], "wind_delta")
```

- [ ] **Run test to verify it fails**

```
.venv/bin/python -m unittest tests.test_suppressions -v
```
Expected: FAIL — `KeyError: 'features'`

- [ ] **Add `_compute_features` helper and update generators in `server/generator.py`**

Add this helper near the top (after imports):
```python
def _compute_features(a: int, b: int, *, extra: dict | None = None) -> dict:
    f = {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b)}
    if extra:
        f.update(extra)
    return f
```

Update `_gen_addition` return block:
```python
    carry = ((a % 10) + (b % 10)) >= 10
    return {
        "prompt": f"What is {a} plus {b}?",
        "expected": float(a + b),
        "parameters": {
            "a": a, "b": b, "carry": carry, "level": level,
            "features": _compute_features(a, b, extra={"has_carry": carry}),
        },
    }
```

Update `_gen_subtraction` return block:
```python
    borrow = (a % 10) < (b % 10)
    return {
        "prompt": f"What is {a} minus {b}?",
        "expected": float(a - b),
        "parameters": {
            "a": a, "b": b, "borrow": borrow, "level": level,
            "features": _compute_features(a, b, extra={"has_borrow": borrow}),
        },
    }
```

Update `_gen_multiplication` return block:
```python
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": _compute_features(a, b)},
    }
```

Update `_gen_division` return block:
```python
    return {
        "prompt": f"What is {a} divided by {b}?",
        "expected": float(a / b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": _compute_features(a, b)},
    }
```

`_gen_percent_of`, `_gen_money_arithmetic`, `_gen_weather_math` — leave unchanged here; percent and money have no meaningful a/b pair; weather is handled below.

- [ ] **Update `server/weather.py`** — add features to delta generators

Add this helper at the bottom of the helpers section (before `_day_label`):
```python
def _delta_features(a: int, b: int, operation: str) -> dict:
    return {
        "abs_diff": abs(a - b),
        "min_operand": min(a, b),
        "max_operand": max(a, b),
        "operation": operation,
    }
```

Update `_temp_delta` return block — add `"features"` key to `parameters`:
```python
    return {
        "prompt": (...),
        "expected": float(diff),
        "parameters": {
            "operation": "temp_delta", "source": "open-meteo",
            "location": location["name"], "warmer": warmer, "cooler": cooler,
            "features": _delta_features(warmer, cooler, "temp_delta"),
        },
    }
```

Update `_wind_delta` return block:
```python
    return {
        "prompt": (...),
        "expected": float(diff),
        "parameters": {
            "operation": "wind_delta", "source": "open-meteo",
            "location": location["name"], "stronger": stronger, "calmer": calmer,
            "features": _delta_features(stronger, calmer, "wind_delta"),
        },
    }
```

Update `_daily_range` return block:
```python
    return {
        "prompt": (...),
        "expected": float(hi - lo),
        "parameters": {
            "operation": "daily_range", "source": "open-meteo",
            "location": location["name"], "high": hi, "low": lo,
            "features": _delta_features(hi, lo, "daily_range"),
        },
    }
```

`_f_to_c_approx` — no pair, leave unchanged (no features key → `{}` default in rules).

- [ ] **Run tests**

```
.venv/bin/python -m unittest tests.test_suppressions -v
```
Expected: all 3 new tests PASS

- [ ] **Commit**

```bash
git add server/generator.py server/weather.py tests/test_suppressions.py
git commit -m "feat: add feature tagging to generators"
```

---

### Task 2: Suppression rules refactor

**Files:**
- Modify: `server/suppressions.py`
- Modify: `suppressions.yaml`
- Modify: `tests/test_suppressions.py`

- [ ] **Write failing tests** — rules use features, not skill_id

Append to `tests/test_suppressions.py`:
```python
from server import suppressions

class SuppressionRuleTests(unittest.TestCase):
    def _params(self, a, b, **extra):
        from server.generator import _compute_features
        return {"a": a, "b": b, "features": _compute_features(a, b, extra=extra or None)}

    def test_trivial_diff_fires_on_features_not_skill(self):
        # abs_diff=1 → suppressed regardless of skill_id
        p = self._params(10, 9)
        self.assertIsNotNone(suppressions.REGISTRY["trivial_diff"]("addition", p))
        self.assertIsNotNone(suppressions.REGISTRY["trivial_diff"]("weather_math", p))

    def test_trivial_diff_does_not_fire_on_large_diff(self):
        p = self._params(17, 2)  # abs_diff=15
        self.assertFalse(suppressions.REGISTRY["trivial_diff"]("subtraction", p))

    def test_small_operand_new_rule(self):
        p = self._params(17, 2)  # min_operand=2
        self.assertTrue(suppressions.REGISTRY["small_operand"]("subtraction", p))

    def test_small_operand_does_not_fire_above_threshold(self):
        p = self._params(17, 3)  # min_operand=3
        self.assertFalse(suppressions.REGISTRY["small_operand"]("subtraction", p))

    def test_by_ten_skill_agnostic(self):
        p = self._params(18, 10)
        self.assertTrue(suppressions.REGISTRY["by_ten"]("subtraction", p))
        self.assertTrue(suppressions.REGISTRY["by_ten"]("addition", p))

    def test_subtract_zero_via_min_operand(self):
        p = self._params(5, 0)
        self.assertTrue(suppressions.REGISTRY["subtract_zero"]("subtraction", p))
        self.assertTrue(suppressions.REGISTRY["subtract_zero"]("addition", p))

    def test_generate_subtraction_never_returns_small_b(self):
        # integration: active suppressions prevent b<=2 from being generated
        from server import suppressions as s
        s.load_active(force=True)
        for _ in range(50):
            result = generator.generate("subtraction")
            b = result["parameters"]["b"]
            self.assertGreater(b, 2, f"b={b} should be suppressed")
```

- [ ] **Run to verify tests fail**

```
.venv/bin/python -m unittest tests.test_suppressions.SuppressionRuleTests -v
```
Expected: most FAIL (rules still have skill guards, `small_operand` doesn't exist)

- [ ] **Rewrite `server/suppressions.py` rules section** — replace everything from `_ROUND_DIFFS` through `equal_operands` with:

```python
_ROUND_DIFFS = frozenset({5, 10, 50, 100, 200, 300, 400, 500, 1000})


def _f(params: dict) -> dict:
    return params.get("features") or {}


@rule("single_digit_small")
def _single_digit_small(skill_id: str, params: dict) -> bool:
    return _f(params).get("max_operand", float("inf")) <= 2


@rule("trivial_diff")
def _trivial_diff(skill_id: str, params: dict) -> bool:
    v = _f(params).get("abs_diff")
    return v is not None and v <= 2


@rule("round_diff")
def _round_diff(skill_id: str, params: dict) -> bool:
    v = _f(params).get("abs_diff")
    return v is not None and v in _ROUND_DIFFS


@rule("subtract_zero")
def _subtract_zero(skill_id: str, params: dict) -> bool:
    return _f(params).get("min_operand") == 0


@rule("by_ten")
def _by_ten(skill_id: str, params: dict) -> bool:
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return m is not None and (m == 10 or M == 10)


@rule("equal_operands")
def _equal_operands(skill_id: str, params: dict) -> bool:
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return m is not None and m == M


@rule("small_operand")
def _small_operand(skill_id: str, params: dict) -> bool:
    return _f(params).get("min_operand", float("inf")) <= 2
```

Remove the `_ab()` helper — it is no longer used.

- [ ] **Update `suppressions.yaml`** — full replacement:

```yaml
# Active suppression rules per skill. Names must exist in
# server/suppressions.py REGISTRY.

addition:
  - single_digit_small
  - small_operand
  - by_ten

subtraction:
  - single_digit_small
  - trivial_diff
  - round_diff
  - subtract_zero
  - equal_operands
  - small_operand
  - by_ten

multiplication:
  - by_ten

weather_math:
  - trivial_diff
  - round_diff
```

- [ ] **Run tests**

```
.venv/bin/python -m unittest tests.test_suppressions -v
```
Expected: all PASS. The integration test may occasionally fail if the sampler hits a wall — re-run once to confirm it's not flaky.

- [ ] **Run full suite to check for regressions**

```
.venv/bin/python -m unittest discover tests -v
```
Expected: 19 existing tests + new suppression tests, all PASS

- [ ] **Commit**

```bash
git add server/suppressions.py suppressions.yaml tests/test_suppressions.py
git commit -m "feat: refactor suppression rules to use features, add small_operand and by_ten for add/sub"
```

---

### Task 3: Pattern analysis + session review improvements

**Files:**
- Modify: `server/session_analysis.py`
- Modify: `tests/test_session_analysis.py`

- [ ] **Write failing tests** — append to `tests/test_session_analysis.py`:

```python
class PatternAnalysisTests(unittest.TestCase):
    def _attempt(self, skill_id, latency_ms, correct=1, **feature_extra):
        f = {"abs_diff": 5, "min_operand": 3, "max_operand": 8}
        f.update(feature_extra)
        return {
            "skill_id": skill_id,
            "resolution_latency_ms": latency_ms,
            "correct": correct,
            "parameters": {"features": f},
        }

    def test_detects_slow_borrow_pattern(self):
        # baseline: 10 no-borrow at 1000ms, 8 borrow at 4000ms → ratio 4x
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 4000, has_borrow=True) for _ in range(8)]
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertTrue(len(results) > 0)
        top = results[0]
        self.assertEqual(top["feature_key"], "has_borrow")
        self.assertEqual(top["feature_value"], True)
        self.assertGreater(top["ratio"], 1.4)
        self.assertEqual(top["n"], 8)

    def test_ignores_groups_below_min_n(self):
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 5000, has_borrow=True) for _ in range(4)]  # n=4 < 5
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertEqual(results, [])

    def test_ignores_low_ratio(self):
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 1300, has_borrow=True) for _ in range(6)]  # ratio 1.3 ≤ 1.4
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertEqual(results, [])

    def test_returns_at_most_3(self):
        # 4 distinct slow patterns
        attempts = []
        for i in range(4):
            attempts += [self._attempt("subtraction", 1000) for _ in range(10)]
            attempts += [self._attempt(f"skill_{i}", 5000, **{f"feat_{i}": True}) for _ in range(6)]
        results = session_analysis.pattern_analysis(attempts)
        self.assertLessEqual(len(results), 3)


class StratificationTests(unittest.TestCase):
    def _sub(self, borrow, latency=1000):
        return {
            "skill_id": "subtraction", "correct": 1,
            "resolution_latency_ms": latency,
            "parameters": {"borrow": borrow},
        }

    def test_splits_subtraction_by_borrow(self):
        attempts = [self._sub(False)] * 3 + [self._sub(True)] * 3
        strat = session_analysis._session_stratification(attempts)
        self.assertEqual(len(strat), 1)
        self.assertEqual(strat[0]["skill_id"], "subtraction")
        labels = {r["label"] for r in strat[0]["rows"]}
        self.assertEqual(labels, {"with borrow", "no borrow"})

    def test_omits_skill_when_one_bucket_too_small(self):
        attempts = [self._sub(False)] * 3 + [self._sub(True)] * 1  # borrow bucket has n=1
        strat = session_analysis._session_stratification(attempts)
        self.assertEqual(strat, [])


class WeakLinesThresholdTests(unittest.TestCase):
    def _focus_stat(self, correct, total):
        return {
            "display": "test", "total": total, "correct": correct,
            "accuracy": correct / total if total else None,
            "fluency_status": "slow" if correct == total else "unknown",
            "median_correct_latency_ms": 3000, "target_ms": 1000,
        }

    def test_weak_lines_requires_min_5_attempts(self):
        # 2/3 correct — old code would fire, new code must not
        stat = self._focus_stat(2, 3)
        lines = session_analysis._weak_lines([stat])
        self.assertEqual(lines, [])

    def test_weak_lines_fires_at_min_5(self):
        stat = self._focus_stat(3, 5)
        lines = session_analysis._weak_lines([stat])
        self.assertEqual(len(lines), 1)
```

- [ ] **Run to verify failures**

```
.venv/bin/python -m unittest tests.test_session_analysis -v
```
Expected: new tests FAIL (functions don't exist yet)

- [ ] **Add `import json` to the top of `server/session_analysis.py`**

The file currently has `import statistics` and `from collections import ...` at the top. Add `import json` to that block.

- [ ] **Add `pattern_analysis` to `server/session_analysis.py`**

Add this function before `_role_counts`:
```python
def pattern_analysis(attempts: list[dict]) -> list[dict]:
    correct = [
        a for a in attempts
        if a.get("correct") and a.get("resolution_latency_ms") is not None
    ]
    if not correct:
        return []
    by_skill: dict[str, list[int]] = defaultdict(list)
    for a in correct:
        by_skill[a["skill_id"]].append(int(a["resolution_latency_ms"]))
    baseline = {sid: statistics.median(lats) for sid, lats in by_skill.items()}
    groups: dict[tuple, list[int]] = defaultdict(list)
    for a in correct:
        params = a.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                continue
        for key, val in (params.get("features") or {}).items():
            groups[(a["skill_id"], key, val)].append(int(a["resolution_latency_ms"]))
    results = []
    for (skill_id, fkey, fval), lats in groups.items():
        if len(lats) < 5:
            continue
        b = baseline.get(skill_id, 0)
        if not b:
            continue
        ratio = statistics.median(lats) / b
        if ratio <= 1.4:
            continue
        results.append({
            "skill_id": skill_id,
            "feature_key": fkey,
            "feature_value": fval,
            "ratio": round(ratio, 2),
            "n": len(lats),
            "group_median_ms": int(statistics.median(lats)),
            "baseline_ms": int(b),
        })
    return sorted(results, key=lambda r: r["ratio"], reverse=True)[:3]
```

- [ ] **Add `_session_stratification` to `server/session_analysis.py`**

Add this before `_role_counts`:
```python
_STRATIFY_CONFIG: dict[str, tuple[str, dict | None]] = {
    "subtraction": ("borrow", {True: "with borrow", False: "no borrow"}),
    "addition": ("carry", {True: "with carry", False: "no carry"}),
    "weather_math": ("operation", None),
}


def _session_stratification(attempts: list[dict]) -> list[dict]:
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for a in attempts:
        by_skill[a.get("skill_id", "")].append(a)
    result = []
    for skill_id, rows in by_skill.items():
        config = _STRATIFY_CONFIG.get(skill_id)
        if not config:
            continue
        key, labels = config
        buckets: dict = defaultdict(list)
        for a in rows:
            params = a.get("parameters") or {}
            val = params.get(key)
            if val is not None:
                buckets[val].append(a)
        valid = {k: v for k, v in buckets.items() if len(v) >= 2}
        if len(valid) < 2:
            continue
        strat_rows = []
        for val, bucket in sorted(valid.items(), key=lambda x: str(x[0])):
            label = (labels or {}).get(val, str(val)) if labels else str(val)
            strat_rows.append({"label": label, "param_value": val, **_attempt_stats(bucket)})
        result.append({"skill_id": skill_id, "param_key": key, "rows": strat_rows})
    return result
```

- [ ] **Add `stratification` to `review_analysis` return dict** — in `review_analysis()`, extend the return dict:

```python
    return {
        "plan": plan,
        "role_stats": role_stats,
        "focus_stats": focus_stats,
        "fluency_gaps": fluency_gaps,
        "slowest_correct": slowest_correct,
        "moved": _moved_lines(focus_stats),
        "still_weak": _weak_lines(focus_stats),
        "next_time": _next_time_line(focus_stats),
        "stratification": _session_stratification(attempts),
    }
```

Do the same for `exploratory_review_analysis` — add `"stratification": _session_stratification(attempts)` to its return.

- [ ] **Fix `_weak_lines` — add n ≥ 5 guard**

```python
def _weak_lines(focus_stats: list[dict]) -> list[str]:
    lines = []
    for row in focus_stats:
        if not row["total"] or row["total"] < 5:
            continue
        if row["accuracy"] is not None and row["accuracy"] < 1:
            lines.append(
                f"{row['display']} still needs work ({row['correct']}/{row['total']} correct)."
            )
        elif row.get("fluency_status") == "slow":
            lines.append(
                f"{row['display']} was correct but still slow — median {row['median_correct_latency_ms']}ms vs target {row['target_ms']}ms."
            )
    return lines[:2]
```

- [ ] **Run tests**

```
.venv/bin/python -m unittest discover tests -v
```
Expected: all PASS

- [ ] **Commit**

```bash
git add server/session_analysis.py tests/test_session_analysis.py
git commit -m "feat: add pattern_analysis, session stratification, fix still_weak threshold"
```

---

### Task 4: Wire patterns into orchestrator + render in frontend

**Files:**
- Modify: `server/orchestrator.py`
- Modify: `web/app.js`

- [ ] **Update `orchestrator.end_session`** — add patterns after analysis is built. In `server/orchestrator.py`, after line `analysis = session_analysis.exploratory_review_analysis(...)` block (i.e., after the `if/elif` that sets `analysis`), add:

```python
        if analysis is not None:
            analysis["patterns"] = session_analysis.pattern_analysis(all_attempts)
```

- [ ] **Update `renderSessionAnalysis` in `web/app.js`** — add Level 2 and Level 3 sections.

Add these three `const` lines immediately before the `return` statement (after `const weak = ...`):
```javascript
    const _FEAT_LABELS = {has_borrow: "borrow", has_carry: "carry", operation: "operation"};
    const stratBits = (a.stratification || []).map((s) => {
      const rowBits = s.rows.map((r) =>
        `<li>${escapeHtml(r.label)}: ${r.correct}/${r.total} correct · ${fmtSec(r.median_latency_ms)}</li>`
      ).join("");
      return `<ul class="analysis-list">${rowBits}</ul>`;
    }).join("");
    const patternBits = (a.patterns || []).map((p) => {
      const feat = _FEAT_LABELS[p.feature_key] || p.feature_key;
      const val = p.feature_value === true ? "yes" : p.feature_value === false ? "no" : escapeHtml(String(p.feature_value));
      return `<li><strong>${escapeHtml(p.skill_id)}</strong> (${feat}=${val}): ${p.ratio.toFixed(1)}× slower · ${fmtSec(p.group_median_ms)} vs ${fmtSec(p.baseline_ms)} (n=${p.n})</li>`;
    }).join("");
```

Then in the return template string, insert two new lines immediately before `<p class="next-up">`:
```javascript
      ${stratBits ? `<h3 class="section tight">This session by type</h3>${stratBits}` : ""}
      ${patternBits ? `<h3 class="section tight">Patterns across all sessions</h3><ul class="analysis-list">${patternBits}</ul>` : ""}
```

The exact edit: in `renderSessionAnalysis`, find the line:
```javascript
      ${weak ? `<h3 class="section tight">Still weak</h3><ul class="analysis-list">${weak}</ul>` : ""}
      <p class="next-up">${escapeHtml(a.next_time || "")}</p>
```
Replace with:
```javascript
      ${weak ? `<h3 class="section tight">Still weak</h3><ul class="analysis-list">${weak}</ul>` : ""}
      ${stratBits ? `<h3 class="section tight">This session by type</h3>${stratBits}` : ""}
      ${patternBits ? `<h3 class="section tight">Patterns across all sessions</h3><ul class="analysis-list">${patternBits}</ul>` : ""}
      <p class="next-up">${escapeHtml(a.next_time || "")}</p>
```

- [ ] **Commit**

```bash
git add server/orchestrator.py web/app.js
git commit -m "feat: wire pattern analysis into session review, render stratification and patterns"
```

---

### Task 5: Migration script + UX fix

**Files:**
- Create: `tools/backfill_features.py`
- Modify: `web/app.js`

- [ ] **Create `tools/backfill_features.py`**

```python
#!/usr/bin/env python3
"""One-time script: backfill features into existing attempt parameters."""
import json, sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"


def _ab_features(params: dict) -> dict:
    a, b = params.get("a"), params.get("b")
    if a is None or b is None:
        return {}
    a, b = int(a), int(b)
    f = {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b)}
    if "borrow" in params:
        f["has_borrow"] = bool(params["borrow"])
    if "carry" in params:
        f["has_carry"] = bool(params["carry"])
    return f


def _weather_features(params: dict) -> dict:
    op = params.get("operation", "")
    pairs = {
        "wind_delta": ("stronger", "calmer"),
        "temp_delta": ("warmer", "cooler"),
        "daily_range": ("high", "low"),
    }
    if op not in pairs:
        return {}
    ka, kb = pairs[op]
    a, b = params.get(ka), params.get(kb)
    if a is None or b is None:
        return {}
    a, b = int(a), int(b)
    return {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b), "operation": op}


conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, skill_id, parameters FROM attempts WHERE parameters IS NOT NULL").fetchall()
updated = 0
for row in rows:
    try:
        params = json.loads(row["parameters"])
    except (TypeError, ValueError):
        continue
    if "features" in params:
        continue
    features = _weather_features(params) if row["skill_id"] == "weather_math" else _ab_features(params)
    if not features:
        continue
    params["features"] = features
    conn.execute("UPDATE attempts SET parameters = ? WHERE id = ?", (json.dumps(params), row["id"]))
    updated += 1
conn.commit()
conn.close()
print(f"Backfilled {updated} attempts.")
```

- [ ] **Run the migration**

```
.venv/bin/python tools/backfill_features.py
```
Expected output: `Backfilled N attempts.` (N should be ~100-130, skipping those already updated by new code and those without a/b params)

- [ ] **Add Enter-key handler for feedback inputs in `web/app.js`**

In the `document.addEventListener("click", ...)` block (around line 641), add a companion `keydown` listener directly after it:

```javascript
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const input = e.target.closest(".fb-reason");
    if (!input) return;
    const row = input.closest(".fb-row");
    if (!row || !row.dataset.sessionId) return;
    const reason = (input.value || "").trim();
    if (!reason) return;
    e.preventDefault();
    const aid = row.dataset.attemptId ? Number(row.dataset.attemptId) : null;
    postFeedback(row, { session_id: row.dataset.sessionId, attempt_id: aid, reason });
  });
```

- [ ] **Run full test suite**

```
.venv/bin/python -m unittest discover tests -v
```
Expected: all PASS

- [ ] **Commit**

```bash
git add tools/backfill_features.py web/app.js
git commit -m "feat: backfill features on historical attempts, fix feedback Enter-key double-submit"
```
