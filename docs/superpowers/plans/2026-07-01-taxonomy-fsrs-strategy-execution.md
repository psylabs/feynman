# Taxonomy v2 + FSRS + Strategy Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the closed-loop scheduler with a two-tier taxonomy (primitive facts / compound procedures), FSRS spaced repetition, feedback-as-scheduler-signal, a verified templated strategy-tip library, and a batch LLM template forge for grounded variety.

**Architecture:** All learning logic is deterministic Python over the existing attempts data. A new `server/taxonomy.py` produces `fact_key_v2` items with column-level carry/borrow features; `server/srs.py` wraps py-fsrs and persists per-item state; the scheduler becomes due-first + weakness + bootstrap with grounded problems applied as "skins" over needed skills. Strategy tips are templated functions whose arithmetic is asserted against the expected answer and grading tolerance before display. The only new LLM use is an offline template-forge batch job whose output is deterministically validated.

**Tech Stack:** Python ≥3.11, FastAPI, SQLite (server/storage.py idempotent migrations), py-fsrs (`fsrs` on PyPI), pytest via `uv run pytest`, OpenAI API (forge only).

## Global Constraints

- Primitive instant-recall target: **onset_latency_ms ≤ 1200** (user decision 2026-07-01). Compound targets stay per-skill `target_latency_ms` on resolution latency.
- **No LLM calls on the answer path.** The forge (Stage 6) is the only new LLM usage and runs offline.
- **Every strategy tip must verify its decomposition against `expected_answer` within the skill's grading tolerance before display.** A tip that fails verification is silently dropped.
- **Do NOT touch STT/homophone handling** (server/stt.py, parser.py) — explicitly deferred by the user.
- DB migrations go in `Storage._migrate()` (server/storage.py:26) and must be idempotent (`try/except sqlite3.OperationalError` pattern used there).
- This is a **public repo**: tests must use synthetic data only — no real merchant names, amounts, or personal data (see AGENTS.md privacy rule). Never commit files under `data/` or `logs/`.
- Run Python tests with `uv run pytest tests/ -q`. All existing tests must stay green after every task.
- Follow existing code style: module-level pure functions, docstring first line describing purpose, no needless classes (see server/diagnosis.py as the reference style).
- Commit after every task; message format `feat(scope): ...` / `fix(scope): ...` matching repo history.

## Execution order and independence

```
Stage 0 (hygiene)            — independent, do first
Stage 1 (taxonomy)           — independent
Stage 2 (FSRS)               — needs Stage 1
Stage 3 (scheduler v2)       — needs Stages 1+2
Stage 4 (feedback semantics) — needs Stages 1+2 (4.1 UI part independent)
Stage 5 (strategy library)   — needs Stage 1 only (parallelizable with 2–4)
Stage 6 (template forge)     — independent of 2–5 (uses generator op registry)
```

Non-goals (do not build): STT/homophone suspect filter; runtime LLM judge; agent memory; feedback UI redesign (B4); calendar integration; per-user FSRS parameter fitting; weekly autopsy report.

---

### Task 0.1: Suppression hygiene — reload + give-up telemetry

**Files:**
- Modify: `server/suppressions.py` (load_active, ~lines 93–115)
- Modify: `server/generator.py` (~lines 334–344, the retry loop)
- Test: `tests/test_suppressions.py` (extend)

**Interfaces:**
- Produces: `suppressions.load_active(force: bool = False) -> dict` now re-reads when the YAML mtime changes (no restart needed).
- Produces: `generator.generate(...)` emits an event dict `{"event": "suppression.give_up", "skill_id": ..., "retries": 20}` via the existing `emit` callable when the 20-retry budget is exhausted (find how `emit` reaches generate — if it doesn't, log via `logging.getLogger("feynman.suppressions").warning(...)` instead; check imports at the top of generator.py).

- [ ] **Step 1: Write failing tests** — in `tests/test_suppressions.py` add:

```python
def test_load_active_reloads_on_mtime_change(tmp_path, monkeypatch):
    yaml_path = tmp_path / "suppressions.yaml"
    yaml_path.write_text("addition: [by_ten]\n")
    monkeypatch.setattr(suppressions, "SUPPRESSIONS_PATH", yaml_path)
    suppressions._active_cache = None  # reset module cache
    first = suppressions.load_active()
    assert first == {"addition": ["by_ten"]}
    import time, os
    yaml_path.write_text("addition: [by_ten, small_operand]\n")
    os.utime(yaml_path, (time.time() + 5, time.time() + 5))  # force mtime forward
    second = suppressions.load_active()
    assert second == {"addition": ["by_ten", "small_operand"]}
```

Adjust the monkeypatched constant name to whatever suppressions.py actually calls its path variable (read the file first).

- [ ] **Step 2: Run** `uv run pytest tests/test_suppressions.py -q` — expect the new test to FAIL (cache never invalidates).
- [ ] **Step 3: Implement** — in `load_active`, store `(_active_cache, _cache_mtime)`; on each call, `stat()` the YAML; if mtime differs, re-read. Keep the `force=True` path working.
- [ ] **Step 4: Implement give-up logging** — in the generator retry loop, when retries exhaust, log one warning with skill_id and the suppressing rule name.
- [ ] **Step 5: Run** `uv run pytest tests/ -q` — all green. **Commit:** `fix(suppressions): reload rules on yaml change, log retry give-up`

### Task 0.2: Remove `category_share` from the live pool

(The redesigned replacement is Task 3.3; between now and then the op simply doesn't appear. Data: 0/7 correct, 6 skips.)

**Files:**
- Modify: `server/scheduler.py` (`_GROUNDED_OPS`, ~line 253)
- Modify: `skills.yaml` (money_arithmetic `operations` list, ~line 61)
- Modify: `server/money.py` only if `generate_problem` random-chooses from its own op list — remove `category_share` from that list too; keep the `_category_share` function in place (Task 3.3 replaces it).
- Test: `tests/test_scheduler_session_plan.py`, `tests/test_money.py`

- [ ] **Step 1:** Add test asserting `"category_share" not in` the ops the scheduler can emit (grep `_GROUNDED_OPS` usage for the exact structure).
- [ ] **Step 2:** Remove the op from the tuple/list(s). Run `uv run pytest tests/ -q`.
- [ ] **Step 3: Commit:** `fix(money): retire category_share op (0/7 accuracy, 6 skips)`

---

## Stage 1 — Taxonomy v2 (`server/taxonomy.py`)

### Task 1.1: Core classify() with column-level carry/borrow features

**Files:**
- Create: `server/taxonomy.py`
- Test: `tests/test_taxonomy.py`

**Interfaces:**
- Produces (used by Stages 2–5):

```python
PRIMITIVE_ONSET_TARGET_MS = 1200

@dataclass(frozen=True)
class Taxon:
    tier: str        # "primitive" | "compound"
    family: str      # assessment/level grain, e.g. "sub.3d-2d.bt"
    key: str         # FSRS item, e.g. "sub:18-4" or "sub.3d-2d.bt"
    features: dict   # {"carry_cols": [...], "borrow_cols": [...], "across_zero": bool, "crosses_hundred": bool}

def classify(skill_id: str, parameters: dict) -> Taxon | None
def carry_columns(a: int, b: int) -> list[int]     # 0=ones, 1=tens, 2=hundreds
def borrow_columns(a: int, b: int) -> list[int]    # requires a >= b >= 0
def family_display(family: str) -> str             # "sub.3d-2d.bt" -> "3-digit − 2-digit, borrow in tens"
```

- [ ] **Step 1: Write failing tests** — `tests/test_taxonomy.py`:

```python
from server.taxonomy import classify, carry_columns, borrow_columns, family_display

def T(skill, **params):
    return classify(skill, params)

def test_carry_columns():
    assert carry_columns(87, 96) == [0, 1]  # 7+6=13 carries ones; 8+9+1=18 carries tens
    assert carry_columns(24, 47) == [0]      # 4+7=11
    assert carry_columns(30, 28) == []       # no carry
    assert carry_columns(98, 99) == [0, 1]

def test_borrow_columns():
    assert borrow_columns(75, 48) == [0]     # ones borrow only
    assert borrow_columns(565, 85) == [1]    # tens borrow only — the blind spot the flag missed
    assert borrow_columns(509, 63) == [1]
    assert borrow_columns(94, 22) == []
    assert borrow_columns(753, 95) == [0, 1]

def test_primitive_addition_within_20():
    t = T("addition", a=8, b=5, carry=True)
    assert t.tier == "primitive" and t.key == "add:5+8" and t.family == "add.bridge10"
    t = T("addition", a=4, b=4)
    assert t.tier == "primitive" and t.family == "add.within20"

def test_compound_addition_families_encode_carry_columns():
    t = T("addition", a=87, b=96)
    assert t.tier == "compound"
    assert t.family == "add.2d+2d.cot"       # carry in Ones and Tens
    assert t.key == t.family                  # compound items schedule at class grain
    assert t.features["crosses_hundred"] is True

def test_primitive_subtraction_within_20():
    t = T("subtraction", a=18, b=4)
    assert t.tier == "primitive" and t.key == "sub:18-4" and t.family == "sub.within20"
    t = T("subtraction", a=14, b=7)
    assert t.family == "sub.borrow20"        # 4-7 needs a borrow

def test_compound_subtraction_families():
    assert T("subtraction", a=565, b=85).family == "sub.3d-2d.bt"
    assert T("subtraction", a=94, b=22).family == "sub.2d-2d.n"
    assert T("subtraction", a=753, b=95).family == "sub.3d-2d.bot"

def test_across_zero_feature():
    t = T("subtraction", a=108, b=16)        # borrowing crosses the 0 in the tens? -> tens borrow from 0? verify: 8-6 ok, 0-1 borrows from hundreds
    assert 1 in t.features["borrow_cols"]
    t2 = T("subtraction", a=306, b=178)      # ones borrow pulls from a 0 tens digit
    assert t2.features["across_zero"] is True

def test_multiplication_table_and_beyond():
    t = T("multiplication", a=9, b=12)
    assert t.tier == "primitive" and t.key == "mul:9x12" and t.family == "mul.x12"
    t = T("multiplication", a=40, b=8)
    assert t.tier == "compound" and t.family == "mul.2dx1d"

def test_division_inverse_facts():
    t = T("division", a=56, b=7)
    assert t.tier == "primitive" and t.key == "div:7x8" and t.family == "div.x8"

def test_grounded_ops_are_compound():
    t = T("money_arithmetic", operation="split_bill", bill_amount=36, people=4)
    assert t.tier == "compound" and t.key == "money:split_bill"
    t = T("weather_math", operation="f_to_c_approx", high=68)
    assert t.key == "weather:f_to_c_approx"

def test_percent():
    t = T("percent_of", percentage=15, base=80)
    assert t.tier == "compound" and t.key == "pct:15" and t.family == "pct"

def test_family_display():
    assert family_display("sub.3d-2d.bt") == "3-digit − 2-digit, borrow in tens"
    assert family_display("add.2d+2d.cot") == "2-digit + 2-digit, carry in ones and tens"
    assert family_display("sub.borrow20") == "subtraction within 20 (borrow)"
    assert family_display("mul.x12") == "×12 table"

def test_unclassifiable_returns_none():
    assert classify("addition", {}) is None
    assert classify("unknown_skill", {"a": 1, "b": 2}) is None
```

- [ ] **Step 2: Run** `uv run pytest tests/test_taxonomy.py -q` — FAIL (module missing).
- [ ] **Step 3: Implement `server/taxonomy.py`:**

```python
"""Taxonomy v2: classify attempts into primitive facts vs compound procedures.

Primitives are finite recall items (judged on onset latency — time to start
speaking). Compounds are procedures (judged on resolution latency vs the
skill's target). Pure functions, no I/O. This is fact_key_v2; the legacy
diagnosis.fact_key stays untouched until Stage 3 swaps the scheduler over.

Prototyped in tools/taxonomy_v2.py against all 409 real attempts.
"""
from dataclasses import dataclass, field

PRIMITIVE_ONSET_TARGET_MS = 1200
MULT_TABLE = set(range(0, 13)) | {15, 20}
_COL = "oth"  # ones, tens, hundreds (column letters for family suffixes)
_COL_WORDS = {"o": "ones", "t": "tens", "h": "hundreds"}


@dataclass(frozen=True)
class Taxon:
    tier: str
    family: str
    key: str
    features: dict = field(default_factory=dict)


def carry_columns(a: int, b: int) -> list[int]:
    cols, carry, i = [], 0, 0
    while a or b or carry:
        s = a % 10 + b % 10 + carry
        carry = 1 if s >= 10 else 0
        if carry:
            cols.append(i)
        a //= 10
        b //= 10
        i += 1
    return cols


def borrow_columns(a: int, b: int) -> list[int]:
    """Columns (0=ones) where computing a-b column-wise needs a borrow. a >= b >= 0."""
    cols, borrow, i = [], 0, 0
    while b or borrow:
        d = a % 10 - b % 10 - borrow
        borrow = 1 if d < 0 else 0
        if borrow:
            cols.append(i)
        a //= 10
        b //= 10
        i += 1
    return cols


def _digits(n: int) -> int:
    n = abs(int(n))
    return 1 if n < 10 else 2 if n < 100 else 3 if n < 1000 else 4


def _suffix(prefix: str, cols: list[int]) -> str:
    if not cols:
        return "n"
    return prefix + "".join(_COL[c] for c in cols if c < len(_COL))


def _across_zero(a: int, cols: list[int]) -> bool:
    """True when any borrow pulls from a zero digit of the minuend."""
    return any((a // 10 ** (c + 1)) % 10 == 0 for c in cols)


def classify(skill_id: str, parameters: dict) -> Taxon | None:
    if not parameters or not isinstance(parameters, dict):
        return None
    a, b = parameters.get("a"), parameters.get("b")

    if skill_id == "addition" and a is not None and b is not None:
        a, b = int(a), int(b)
        lo, hi = sorted([a, b])
        cols = carry_columns(a, b)
        if a + b <= 20:
            fam = "add.bridge10" if cols else "add.within20"
            return Taxon("primitive", fam, f"add:{lo}+{hi}")
        fam = f"add.{_digits(lo)}d+{_digits(hi)}d.{_suffix('c', cols)}"
        feats = {"carry_cols": cols,
                 "crosses_hundred": (a < 100 and b < 100 and a + b >= 100)}
        return Taxon("compound", fam, fam, feats)

    if skill_id == "subtraction" and a is not None and b is not None:
        a, b = int(a), int(b)
        cols = borrow_columns(a, b)
        if a <= 20:
            fam = "sub.borrow20" if cols else "sub.within20"
            return Taxon("primitive", fam, f"sub:{a}-{b}")
        fam = f"sub.{_digits(a)}d-{_digits(b)}d.{_suffix('b', cols)}"
        feats = {"borrow_cols": cols, "across_zero": _across_zero(a, cols),
                 "crosses_hundred": (a >= 100) != (a - b >= 100)}
        return Taxon("compound", fam, fam, feats)

    if skill_id == "multiplication" and a is not None and b is not None:
        lo, hi = sorted([int(a), int(b)])
        if lo in MULT_TABLE and hi in MULT_TABLE:
            return Taxon("primitive", f"mul.x{hi}", f"mul:{lo}x{hi}")
        fam = f"mul.{_digits(lo)}dx{_digits(hi)}d"
        return Taxon("compound", fam, fam)

    if skill_id == "division" and a is not None and b is not None:
        a, b = int(a), int(b)
        if b and a % b == 0:
            q = a // b
            lo, hi = sorted([b, q])
            if lo in MULT_TABLE and hi in MULT_TABLE:
                return Taxon("primitive", f"div.x{hi}", f"div:{lo}x{hi}")
        return Taxon("compound", "div.multi", "div.multi")

    if skill_id == "percent_of":
        pct = parameters.get("percentage")
        if pct is None:
            return None
        return Taxon("compound", "pct", f"pct:{int(pct)}")

    if skill_id in ("money_arithmetic", "weather_math"):
        op = parameters.get("operation")
        if not op:
            return None
        prefix = "money" if skill_id == "money_arithmetic" else "weather"
        return Taxon("compound", f"{prefix}.{op}", f"{prefix}:{op}")

    return None


def family_display(family: str) -> str:
    fixed = {
        "add.within20": "addition within 20",
        "add.bridge10": "addition within 20 (bridging 10)",
        "sub.within20": "subtraction within 20",
        "sub.borrow20": "subtraction within 20 (borrow)",
        "div.multi": "multi-digit division",
        "pct": "percent of a number",
    }
    if family in fixed:
        return fixed[family]
    if family.startswith(("mul.x", "div.x")):
        return f"×{family.split('x')[1]} table" if family.startswith("mul") else f"÷{family.split('x')[1]} family"
    parts = family.split(".")
    if len(parts) == 3 and parts[0] in ("add", "sub"):
        op = "+" if parts[0] == "add" else "−"
        sep = "+" if "+" in parts[1] else "-"
        lo, hi = parts[1].split(sep)
        shape = f"{lo.rstrip('d')}-digit {op} {hi.rstrip('d')}-digit"
        suf = parts[2]
        if suf == "n":
            detail = "no carry" if parts[0] == "add" else "no borrow"
        else:
            word = "carry" if suf[0] == "c" else "borrow"
            places = " and ".join(_COL_WORDS[ch] for ch in suf[1:])
            detail = f"{word} in {places}"
        return f"{shape}, {detail}"
    if family.startswith(("money.", "weather.")):
        return family.replace(".", ": ").replace("_", " ")
    return family
```

Note for the implementer: `family_display` for the `add.2d+2d.cot` case must yield exactly `"2-digit + 2-digit, carry in ones and tens"` and `sub.3d-2d.bt` → `"3-digit − 2-digit, borrow in tens"` (the − is U+2212 to match the test). Clean up the digit-shape string building until the tests pass — the tests are the spec.

- [ ] **Step 4: Run** `uv run pytest tests/test_taxonomy.py -q` — PASS. Then full suite.
- [ ] **Step 5: Commit:** `feat(taxonomy): fact_key_v2 with column-level carry/borrow classification`

### Task 1.2: Point the assessment tool at the server module

**Files:**
- Modify: `tools/taxonomy_v2.py` (delete its local `classify`/`_dig`, import from `server.taxonomy`; change `PRIMITIVE_ONSET_TARGET_MS` import; families now come from the new classifier so the report shows carry/borrow columns)
- Modify: the report's family lines to use `taxonomy.family_display(fam)` alongside the raw family id.

- [ ] **Step 1:** Make the edit; keep the strategy functions in the tool for now (Stage 5 moves them to `server/strategies.py`, after which this tool imports from there too).
- [ ] **Step 2:** Run `uv run python tools/taxonomy_v2.py` — output shows families like `sub.3d-2d.bt  (3-digit − 2-digit, borrow in tens)` and the 1200ms target. Sanity-check no crash on all 409 attempts.
- [ ] **Step 3: Commit:** `refactor(tools): assessment tool uses server.taxonomy`

---

## Stage 2 — FSRS foundation

### Task 2.1: `server/srs.py` wrapper + `item_state` table

**Files:**
- Modify: `pyproject.toml` — add `"fsrs>=5.0"` to dependencies; run `uv sync`.
- Create: `server/srs.py`
- Modify: `server/storage.py` — migration + three methods
- Test: `tests/test_srs.py`

**Interfaces:**
- Produces:

```python
# server/srs.py
def rate(correct: bool, latency_ms: int | None, target_ms: int) -> "Rating"
    # wrong -> Again; correct & latency > target -> Hard; correct & <= target -> Good
    # (Easy is only ever assigned via feedback, Stage 4)
def review(card_json: str | None, rating, when: datetime) -> tuple[str, float]
    # returns (new_card_json, due_at_epoch_seconds)

# storage methods
def upsert_item_state(self, user_id, item_key, tier, family, card_json, due_at, rating: int) -> None
def get_item_state(self, user_id, item_key) -> dict | None
def due_items(self, user_id, now: float, limit: int = 50) -> list[dict]  # most overdue first
```

- [ ] **Step 1: Verify the fsrs API before writing code.** Run:

```bash
uv run python -c "
from fsrs import Scheduler, Card, Rating
from datetime import datetime, timezone
s = Scheduler()
c = Card()
c2, log = s.review_card(c, Rating.Good, datetime.now(timezone.utc))
print(type(c2.due), c2.to_dict() is not None, Card.from_dict(c2.to_dict()).due == c2.due)
"
```

If this import shape fails (the library API moved), read `uv run python -c "import fsrs, inspect; print(inspect.getsource(fsrs)[:2000])"` and adapt `srs.py` — but keep the `srs.py` public interface above exactly as specified, so nothing else changes.

- [ ] **Step 2: Write failing tests** — `tests/test_srs.py`:

```python
from datetime import datetime, timezone, timedelta
from server import srs
from fsrs import Rating

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)

def test_rate_mapping():
    assert srs.rate(False, 500, 1200) == Rating.Again
    assert srs.rate(True, 3000, 1200) == Rating.Hard
    assert srs.rate(True, 800, 1200) == Rating.Good
    assert srs.rate(True, None, 1200) == Rating.Good  # missing latency: don't punish

def test_review_roundtrip_and_growth():
    card1, due1 = srs.review(None, Rating.Good, NOW)
    assert isinstance(card1, str) and due1 > NOW.timestamp()
    card2, due2 = srs.review(card1, Rating.Good, NOW + timedelta(days=1))
    assert due2 > due1  # interval grows on success

def test_again_resets_sooner_than_good():
    good, due_g = srs.review(None, Rating.Good, NOW)
    bad, due_b = srs.review(None, Rating.Again, NOW)
    assert due_b < due_g

def test_item_state_storage(tmp_path):
    from server.storage import Storage
    st = Storage(tmp_path / "t.db")
    st.upsert_item_state("u1", "mul:6x7", "primitive", "mul.x7", "{}", 1000.0, 3)
    st.upsert_item_state("u1", "sub:18-4", "primitive", "sub.within20", "{}", 500.0, 1)
    st.upsert_item_state("u1", "mul:6x7", "primitive", "mul.x7", "{}", 2000.0, 3)  # upsert
    due = st.due_items("u1", now=1500.0)
    assert [d["item_key"] for d in due] == ["sub:18-4"]  # only overdue, sorted
    assert st.get_item_state("u1", "mul:6x7")["due_at"] == 2000.0
```

Match the `Storage(...)` constructor signature to how existing tests build it (see tests/test_storage_attempts.py — copy its fixture pattern exactly).

- [ ] **Step 3: Run** — FAIL. **Step 4: Implement.**

`server/srs.py`:

```python
"""FSRS wrapper: single place that touches the py-fsrs library.

Cards are stored as JSON blobs in item_state; the scheduler only ever sees
(item_key, due_at). Default FSRS parameters — do not fit personal params
at this data scale (~400 attempts).
"""
import json
from datetime import datetime, timezone

from fsrs import Scheduler, Card, Rating

_scheduler = Scheduler()


def rate(correct: bool, latency_ms: int | None, target_ms: int) -> Rating:
    if not correct:
        return Rating.Again
    if latency_ms is not None and latency_ms > target_ms:
        return Rating.Hard
    return Rating.Good


def review(card_json: str | None, rating: Rating, when: datetime) -> tuple[str, float]:
    card = Card.from_dict(json.loads(card_json)) if card_json else Card()
    card, _log = _scheduler.review_card(card, rating, when)
    return json.dumps(card.to_dict()), card.due.replace(tzinfo=timezone.utc).timestamp()
```

(If `card.due` is already tz-aware, drop the `.replace` — assert in the Step-1 probe.)

Storage migration (inside `_migrate`, following its existing idempotent pattern):

```sql
CREATE TABLE IF NOT EXISTS item_state (
  user_id TEXT NOT NULL,
  item_key TEXT NOT NULL,
  tier TEXT NOT NULL,
  family TEXT NOT NULL,
  card_json TEXT NOT NULL,
  due_at REAL NOT NULL,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  last_rating INTEGER,
  updated_at REAL NOT NULL,
  PRIMARY KEY (user_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_item_state_due ON item_state(user_id, due_at);
```

`upsert_item_state` increments `reps`, increments `lapses` when `rating == 1`, sets `last_rating`, `updated_at = time.time()`. `due_items` = `SELECT * FROM item_state WHERE user_id=? AND due_at <= ? ORDER BY due_at ASC LIMIT ?`.

- [ ] **Step 5: Run** full suite. **Commit:** `feat(srs): py-fsrs wrapper and item_state persistence`

### Task 2.2: Grade hook on every attempt

**Files:**
- Modify: `server/orchestrator.py` — the two per-attempt sites where `mastery.update` is called after an attempt insert (lines ~346 and ~501; NOT the session-end recompute at ~589). Read the surrounding code to find where the attempt row (with parameters, correct, latencies) is in scope.
- Test: `tests/test_orchestrator_session_plan.py` pattern for building an orchestrator, or a focused new `tests/test_srs_hook.py` using the same fixtures.

**Interfaces:**
- Produces: `orchestrator._record_srs(user_id, skill_id, parameters, correct, onset_ms, resolution_ms, target_ms, item_key_override=None)` — classifies via `taxonomy.classify`, picks latency by tier (onset for primitive, resolution for compound), rates via `srs.rate` (primitive target = `taxonomy.PRIMITIVE_ONSET_TARGET_MS`, compound target = skill `target_latency_ms`), reviews, upserts. Silently returns on `classify(...) is None`. `item_key_override` is used by Stage 3 skins to credit the skeleton item as well.
- Skipped attempts (`skipped=1`) must NOT produce a review.

- [ ] **Step 1: Write failing test** — drive one graded attempt through the orchestrator (copy the arrangement from an existing orchestrator test) and assert `storage.get_item_state(user, "sub:18-4")` exists with `reps == 1`, and that a wrong answer yields `last_rating == 1`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement `_record_srs` and call it from both sites. **Step 4:** Full suite green.
- [ ] **Step 5: Commit:** `feat(srs): grade every attempt into item_state`

### Task 2.3: Backfill from attempt history

**Files:**
- Create: `tools/backfill_srs.py` (follow the shape of `tools/backfill_features.py` — module docstring, direct sqlite3, print summary)

**Behavior:** Iterate all attempts ordered by `created_at`; for each non-skipped attempt with a classifiable taxon, apply the same rate→review→upsert pipeline using `datetime.fromtimestamp(created_at, tz=timezone.utc)` as the review time, keyed by the session's user (join sessions for user_id — see how storage resolves user_id elsewhere). Idempotent: `DELETE FROM item_state` for the user first, then replay.

- [ ] **Step 1:** Write it. **Step 2:** Run `uv run python tools/backfill_srs.py` against the real DB. Expected output: a summary like `replayed 380 attempts into N item states (M primitive, K compound)`. Sanity checks to print: `mul:9x12` exists with lapses ≥ 1; `sub.3d-2d.*` families exist; nothing due before 2026-05-02.
- [ ] **Step 3: Commit** (script only — never commit the DB): `feat(srs): attempt-history backfill script`

---

## Stage 3 — Scheduler v2 (due-first, need-driven, grounded skins)

### Task 3.1: Family stats and evidence-based level rule

**Files:**
- Create: `server/family_stats.py`
- Test: `tests/test_family_stats.py`

**Interfaces:**
- Produces:

```python
def family_stats(attempts: list[dict]) -> dict[str, dict]
    # {family: {"tier", "n", "acc", "median_ms", "last_seen_at"}} — latency field
    # chosen by tier (onset for primitive, resolution for compound), correct-only median
def weak_families(stats: dict, skill_targets: dict[str, int], limit: int = 10) -> list[str]
    # families with n>=5 sorted worst-first by (acc, then latency-over-target ratio);
    # excludes anything with acc>=0.9 AND within target
def family_level(attempts: list[dict], family: str) -> int
    # evidence rule over that family's attempts:
    #   start at 1; advance +1 (cap 3) for each level where the last 10+ attempts
    #   at that level have acc>=0.9 and median latency within target;
    #   drop -1 (floor 1) if last 10 attempts at current level have acc<0.6
```

`attempts` rows are the dicts storage returns (must include parameters JSON already parsed or parse here — match what `diagnosis.compute_fact_stats` consumes, see server/diagnosis.py:285).

- [ ] **Step 1: Write failing tests** with synthetic attempts (helper that fabricates attempt dicts with given family-producing params, correct flags, latencies, levels). Cover: level advance on 10 strong L2 attempts; level drop on 10 weak; weak_families ordering puts a 0.45-acc family before a slow-but-accurate one.
- [ ] **Step 2–4:** FAIL → implement → PASS, full suite. **Commit:** `feat(scheduler): taxonomy family stats and evidence-based levels`

### Task 3.2: Rewrite `build_session_plan`

**Files:**
- Modify: `server/scheduler.py` — replace the allocation logic in `build_session_plan` (lines ~96–223), delete `_build_grounded_slots`'s hardcoded tip/split staples (~315–318), delete the retention quota, keep `_spread_duplicates_with_alternation` and the slot dict shape unchanged (same keys: `skill_id`, `fact_key`, `role`, `level`, `target`, `reason` — read `pick_drill` to confirm the exact set; orchestrator must not need changes).
- Test: `tests/test_scheduler_session_plan.py` (rewrite expectations), new cases below.

**Selection algorithm (implement exactly):**

```python
HEALTHY_GROUNDED_SHARE = 0.35   # "healthy amount", not a quota — user decision 2026-07-01
RECENT_KEY_WINDOW = 15          # last N attempt keys excluded unless overdue
BOOTSTRAP_ROLE = "bootstrap"

# Skin map: family-prefix -> grounded ops that exercise it (skeleton/skin split)
SKINS = {
    "sub.2d": [("weather_math", "temp_delta"), ("weather_math", "daily_range")],
    "sub.3d": [("money_arithmetic", "category_difference")],
    "add.2d": [("money_arithmetic", "charge_total")],
    "add.3d": [("money_arithmetic", "charge_total")],
    "pct":    [("money_arithmetic", "restaurant_tip_15"), ("money_arithmetic", "category_amount")],
    "div":    [("money_arithmetic", "split_bill")],
}

def build_session_plan(storage, user_id, length, emit):
    now = time.time()
    attempts = storage.all_attempts_for_user(user_id, limit=500)
    recent_keys = {taxonomy-classified key of the last RECENT_KEY_WINDOW attempts}
    cooldown = storage.active_cooldowns(user_id, now)        # Stage 4; returns set(); stub -> set() until then
    excluded = storage.excluded_keys(user_id)                # Stage 4; stub -> set() until then
    slots = []

    # 1. Due items, most overdue first (never blocked by recency if > 1 day overdue)
    for item in storage.due_items(user_id, now, limit=length * 2):
        if len(slots) >= length: break
        if item["item_key"] in excluded: continue
        overdue_days = (now - item["due_at"]) / 86400
        if item["item_key"] in (recent_keys | cooldown) and overdue_days < 1: continue
        slots.append(_slot_for_item(item, role="due", attempts=attempts))

    # 2. One bootstrap slot per session while untested material exists
    #    (division facts, multiplication table rows with n<5 — derive from
    #    family_stats: any primitive family in BOOTSTRAP_FAMILIES with n<5)
    #    BOOTSTRAP_FAMILIES = [f"mul.x{n}" for n in (2,...,12,15,20)] + [f"div.x{n}" ...]
    #                         + ["add.bridge10", "sub.borrow20"]
    if len(slots) < length and untested:
        slots.append(_bootstrap_slot(pick_round_robin(untested)))

    # 3. Weak-family drills fill the rest (weak_families order, round-robin,
    #    level from family_level, skipping cooldown/excluded/recent)
    # 4. If STILL short (new user), fall back to existing _cold_start_pick per slot.

    apply_skins(slots, length)   # step 5, below
    return _spread_duplicates_with_alternation(slots)

def apply_skins(slots, length):
    """Render eligible slots through grounded generators until the session has
    ceil(length * HEALTHY_GROUNDED_SHARE) grounded slots (or eligibility runs out).
    A skinned slot keeps `covers=<original item_key>` so grading credits both."""
    target = math.ceil(length * HEALTHY_GROUNDED_SHARE)
    grounded = sum(1 for s in slots if s["skill_id"] in ("money_arithmetic", "weather_math"))
    for s in slots:
        if grounded >= target: break
        # every slot carries `family` (set by _slot_for_item / weak-family picks)
        prefix = next((p for p in SKINS if s.get("family", "").startswith(p)), None)
        if prefix is None or s["skill_id"] in ("money_arithmetic", "weather_math"): continue
        skill_id, op = random.choice(SKINS[prefix])
        s.update(skill_id=skill_id, covers=s["fact_key"],
                 fact_key=f"{'money' if skill_id=='money_arithmetic' else 'weather'}:{op}",
                 target={"operation": op}, role=s["role"] + "+skin")
        grounded += 1
```

The `covers` key flows through the slot → question meta → `_record_srs(item_key_override=covers)` so a skinned attempt reviews BOTH the grounded op item and the skeleton item with the same rating (two `upsert_item_state` calls). Wire that in orchestrator where the slot/plan metadata is available at grading time (the plan slot is attached to the active question — read `orchestrator.next_question` to find where slot fields live).

- [ ] **Step 1: Write failing tests** (synthetic storage with seeded item_state + attempts):
  - overdue items appear before weak-family drills;
  - a session for a user with zero division attempts contains ≥1 `role=="bootstrap"` slot with a `div:` or `mul:` fact_key;
  - a 7-slot session ends up with ≥2 and ≤4 grounded slots when skins are eligible (`ceil(7*0.35)=3` target, flexible when eligibility is short);
  - no hardcoded tip/split staples: build 10 plans, assert `restaurant_tip_15` is NOT in every one;
  - a key attempted in the last 15 attempts and not overdue does not reappear;
  - slot dict shape is unchanged (orchestrator smoke test still passes).
- [ ] **Step 2–4:** FAIL → implement → PASS. Update `tests/test_seed_pack.py` if the seed pack asserts old plan composition (it reuses the scheduler — verify it still produces a full pack).
- [ ] **Step 5: Commit:** `feat(scheduler): due-first need-driven plans with grounded skins`

### Task 3.3: `category_amount` (redesigned category_share) + exact-fact targeting

**Files:**
- Modify: `server/money.py` — add `_category_amount`, register op `category_amount`; remove `category_share` from any internal op list (function may be deleted now).
- Modify: `skills.yaml` — money operations list: add `category_amount`.
- Test: `tests/test_money.py`

**Spec:** From monthly category totals (same data source `_category_share` used): round the month total to the nearest $100 (reuse `_swag`-style rounding, money.py:458); compute the category's true share; snap to the nearest friendly percent in `{10, 15, 20, 25, 30, 40, 50}`; only emit if the snap error ≤ 4 percentage points (else pick another category/month, max 10 tries, else fall back to another op). Prompt: `"In {month_name}, about {pct}% of your ${total} spend was {category}. About how much is that?"` — `expected = total * pct / 100` (a dollar amount, so the existing `money` tolerance rule applies). Parameters: `{"operation": "category_amount", "percentage": pct, "total": total, "category": ..., "month": ..., "level": ...}`. This tests percent anchors with clean numbers — the failure mode of the old op (ugly long division) is designed out.

- [ ] **Step 1:** Failing test with a synthetic transactions CSV fixture (see how test_money.py builds fixtures): assert prompt shape, expected value, snap rejection when no category is within 4pp of a friendly percent.
- [ ] **Step 2–4:** Implement → green. **Commit:** `feat(money): category_amount replaces category_share`

---

## Stage 4 — Feedback as scheduler signal

### Task 4.1: Structured reason chips end-to-end

**Files:**
- Modify: `web/app.js` — `renderFeedbackList` (~line 1379) and `postFeedback` (~1445): add four chip buttons per row — labels `too easy`, `too hard`, `seen too often`, `bad problem` — each posting `{..., reason_code: "<code>"}`; keep the free-text input as the optional 5th path. Codes: `too_easy | too_hard | seen_too_often | bad_problem`.
- Modify: `server/main.py` `/feedback` route (~382–408): accept optional `reason_code`, validate against the four codes.
- Modify: `server/storage.py`: migration `ALTER TABLE user_feedback ADD COLUMN reason_code TEXT` (idempotent try/except) + `insert_user_feedback` passes it through.
- Modify: `server/sync.py` `_record_feedback` (~19–51): pass `reason_code` from offline payloads too.
- Test: `tests/test_sync_records.py` (extend), new `tests/test_feedback_route.py` if there's an API-test pattern to copy (check how other routes are tested; if none, test storage+sync layers only).

- [ ] **Step 1–4:** Failing tests for storage round-trip and sync payload → implement → green.
- [ ] **Step 5: Commit:** `feat(feedback): structured reason codes from review chips`

### Task 4.2: Apply feedback semantics (the table finally gets read)

**Files:**
- Create: `server/feedback_semantics.py`
- Modify: `server/main.py` `/feedback` route — after insert, call `feedback_semantics.apply(...)`.
- Modify: `server/storage.py` — migration + methods for the two new tables.
- Test: `tests/test_feedback_semantics.py`

**Tables:**

```sql
CREATE TABLE IF NOT EXISTS key_cooldown (
  user_id TEXT NOT NULL, item_key TEXT NOT NULL, until REAL NOT NULL,
  PRIMARY KEY (user_id, item_key));
CREATE TABLE IF NOT EXISTS excluded_keys (
  user_id TEXT NOT NULL, item_key TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL,
  PRIMARY KEY (user_id, item_key));
```

**Semantics (exact):** given the feedback row's attempt (classify its taxon; no-op if unclassifiable or attempt_id is null):
- `too_easy` → `srs.review(card, Rating.Easy, now)` on that item (long interval; this IS the suppression the user kept asking for).
- `too_hard` → no state change (weak_families already drills it); store only.
- `seen_too_often` → upsert `key_cooldown(user, key, until=now + 7*86400)`.
- `bad_problem` → insert into a per-key tally (reuse user_feedback rows: `SELECT COUNT(DISTINCT date(created_at,'unixepoch'))` for that key with reason_code='bad_problem'); when ≥ 2 distinct days → insert into `excluded_keys`.
- A bare `thumb=-1` with no reason_code → treat as `too_easy` **only when** the attempt was correct and within latency target (that matches every historical case); otherwise store only.
- Storage exposes `active_cooldowns(user_id, now) -> set[str]` and `excluded_keys(user_id) -> set[str]`; Task 3.2's stubs now become real calls (remove the stubs).

- [ ] **Step 1: Failing tests** for each mapping + the two-distinct-days exclusion rule + cooldown expiry.
- [ ] **Step 2–4:** Implement → green. **Commit:** `feat(feedback): feedback drives FSRS, cooldowns, and exclusions`

### Task 4.3: Backfill the 93 historical feedback rows

**Files:**
- Create: `tools/backfill_feedback_codes.py`

**Mapping (deterministic keywords, case-insensitive, first match wins):** reason contains `simple`/`easy`/`trivial` → `too_easy`; contains `frequent`/`repet`/`just asked` → `seen_too_often`; contains `hard` → `too_hard`; else NULL (leave for manual review, print them). After coding, run `feedback_semantics.apply` per row in chronological order. Print a summary table of codes assigned.

- [ ] **Step 1:** Write + run against real DB. Expected: ~20+ rows coded `too_easy`, several `seen_too_often`, a handful uncoded.
- [ ] **Step 2: Commit** the script: `feat(feedback): historical reason-code backfill`

---

## Stage 5 — Strategy library (`server/strategies.py`)

### Task 5.1: Framework + full rule set

**Files:**
- Create: `server/strategies.py`
- Test: `tests/test_strategies.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class Strategy:
    name: str
    families: tuple[str, ...]           # family PREFIXES this applies to
    build: Callable[[dict, float], str | None]   # (parameters, expected) -> tip or None

def tips_for(skill_id: str, parameters: dict, expected: float,
             tolerance: dict | None) -> list[str]
    # classify -> collect strategies whose family prefix matches -> call build
    # inside try/except (AssertionError, TypeError, KeyError, ZeroDivisionError)
    # -> return at most 2 tips. A build that can't verify returns None or raises.
```

**Verification rule:** every `build` computes its decomposition numerically and `assert`s it equals `expected` (exact for exact-tolerance skills; within `max(1, 0.01*expected)` for money ops that legitimately estimate — encode the money check as a shared `_money_ok(est, expected)` helper). The framework's try/except is the safety net; the assert is the contract.

**The rule set.** Port the 14 working strategies from `tools/taxonomy_v2.py` verbatim (count_up, subtract_in_hops, left_to_right_sub incl. trailing-zero variant, bridge_ten, compensation_add, add_tens_then_ones, times_9, times_12, near_square, tip_15, split_round_friendly, round_then_total, round_both_subtract, f_to_c) and add these 12 (each with the same assert-verified shape):

| name | families | decomposition (must assert) | example rendering |
|---|---|---|---|
| `complement_100` | sub.3d, sub.2d | a ∈ {100, 1000}: ones-digits complement to 10, rest to 9 | "100−37: digits to 9, last to 10 → 63" |
| `doubles_anchor` | add.within20, add.bridge10, add.2d | \|a−b\| ≤ 2: 2·min + diff | "7+8: double 7 is 14, +1 → 15" |
| `times_4` | mul.x4 | double twice | "4×16: 16→32→64" |
| `times_5` | mul.x5 | ×10 then halve | "5×18: 180/2 = 90" |
| `times_8` | mul.x8 | double three times | "8×7: 7→14→28→56" |
| `times_11` | mul.x11 | 2-digit n: digits a,b → a,(a+b),b when a+b<10; else ×10+n | "11×23: 2_(2+3)_3 → 253" |
| `times_15` | mul.x15 | ×10 + half of that | "15×8: 80+40=120" |
| `times_20` | mul.x20 | ×2 then ×10 | "20×7: 14→140" |
| `div_as_inverse` | div | render as missing-factor | "56÷7: what ×7 makes 56? 8" |
| `pct_anchor` | pct, money.restaurant_tip_15, money.category_amount | 10%→shift; 5%→half that; 15%→10%+half; 20%→10%×2; 25%→quarter; 50%→half (dispatch on the percentage) | "25% of 60: quarter it → 15" |
| `sub_same_ones` | sub.2d, sub.3d | b's ones == a's ones: pure tens subtraction | "94−24: ones cancel, 9−2 tens → 70" |
| `f_between` | weather.daily_range, weather.temp_delta | count up from low to high | "high 62, low 53: 53→62 is 9" |

Every strategy function is small (5–12 lines); write all of them out — no stubs. Where the table's decomposition is ambiguous, the assert forces the correct arithmetic; the rendering strings follow the existing 14's voice (imperative, numbers inline, one sentence).

- [ ] **Step 1: Failing golden tests** — parametrized table of `(skill_id, params, expected, must_contain_substring)` covering every one of the 26 strategies with at least one real triggering case and one non-triggering case (returns no tip). Plus the safety property test:

```python
@pytest.mark.parametrize("seed", range(50))
def test_no_strategy_ever_lies(seed):
    rng = random.Random(seed)
    a, b = rng.randint(2, 999), rng.randint(2, 99)
    a, b = max(a, b), min(a, b)
    for skill, params, expected in [
        ("subtraction", {"a": a, "b": b}, float(a - b)),
        ("addition", {"a": a, "b": b}, float(a + b)),
        ("multiplication", {"a": rng.randint(2, 20), "b": rng.randint(2, 20)}, None),
    ]:
        if expected is None:
            expected = float(params["a"] * params["b"])
        for tip in strategies.tips_for(skill, params, expected, None):
            assert str(int(expected)) in tip or f"{expected:g}" in tip
    # the final claimed answer must literally appear in every tip
```

- [ ] **Step 2–4:** FAIL → implement all 26 → green. **Step 5:** Point `tools/taxonomy_v2.py` at `server.strategies` (delete its local copies). Re-run the tool; coverage line should report ≥ the previous 86/99.
- [ ] **Step 6: Commit:** `feat(strategies): verified 26-rule mental-math strategy library`

### Task 5.2: Tips in the post-session review

**Files:**
- Modify: `server/session_analysis.py` — in `review_analysis` (~line 40), for each reviewed attempt that is wrong OR slow (correct with latency > tier-appropriate target × 1.25), attach `"tip": strategies.tips_for(...)[0]` (first verified tip, or omit the key).
- Modify: `web/app.js` — the review-row renderer (`renderReview` ~1350 / `renderFeedbackList` ~1379): when an attempt has `tip`, render it as a muted line under the row. No other UI change (B4 stays out of scope).
- Test: `tests/test_session_analysis.py` (extend with one wrong-attempt case asserting the tip lands in the payload).

- [ ] **Steps:** failing test → implement → green → **Commit:** `feat(review): verified strategy tips on wrong/slow attempts`

---

## Stage 6 — Template forge (batch LLM, deterministically validated)

### Task 6.1: `tools/template_forge.py`

**Files:**
- Create: `tools/template_forge.py`
- Create: `server/forge_ops.py` (the closed compute registry — importable by both the forge validator and the generator)
- Test: `tests/test_template_forge.py` (LLM mocked)

**Interfaces:**

```python
# server/forge_ops.py — closed registry; the LLM can only pick from these
OPS: dict[str, Callable[..., float]] = {
    "sub":       lambda a, b: a - b,
    "add_list":  lambda *xs: sum(xs),
    "pct_of":    lambda pct, base: pct * base / 100,
    "div":       lambda a, b: a / b,
    "delta":     lambda a, b: abs(a - b),
}

# pool entry (data/template_pool.json, list of):
{"id": str, "prompt": str, "skill_id": "money_arithmetic"|"weather_math",
 "operation": str,            # must be an existing grounded op (drives taxonomy key)
 "op": str, "args": [numbers], # forge_ops computation
 "source": str, "created_at": float, "used": false}
```

**Forge behavior (`uv run python tools/template_forge.py [--dry-run] [--review] [--n 20]`):**
1. Load context rows: recent Plaid transactions (reuse `money.py`'s loading functions — import, don't duplicate) and the cached weather forecast (`weather.py`).
2. One chat call to OpenAI (`OPENAI_API_KEY`, model from env `FEYNMAN_FORGE_MODEL`, default `gpt-4o-mini`, `response_format={"type": "json_object"}`). System prompt states: produce `{"candidates": [...]}` phrasings for mental-math problems grounded in the provided rows; numbers must be pre-rounded to clean integers; each candidate must name `op` from the provided registry list, `args`, and an `operation` from the provided grounded-op list; vary sentence framing, not arithmetic.
3. **Validate each candidate deterministically, rejecting on any failure:** `op` in OPS; `expected = OPS[op](*args)` computes without error; every number token ≥ 10 appearing in the prompt is a member of `args` (regex `\d+(?:\.\d+)?` over prompt, compare as floats — this blocks hallucinated numbers); `operation` is a real grounded op; prompt ≤ 180 chars and ends with `?`; not a duplicate (case-folded prompt) of an existing pool entry.
4. `--review` prints accepted candidates and asks for `y/n` before writing; `--dry-run` prints and writes nothing. Default appends accepted entries to `data/template_pool.json`. Print `accepted X / rejected Y (reasons: ...)`.

- [ ] **Step 1: Failing tests** — mock the OpenAI client (see how `tests/test_stt.py` fakes OpenAI); feed a canned candidate list containing: one valid, one with a hallucinated number in the prompt, one with unknown op, one duplicate. Assert exactly one accepted and the rejection reasons.
- [ ] **Step 2–4:** Implement → green. **Commit:** `feat(forge): batch LLM template forge with deterministic validation`

### Task 6.2: Generators consume the pool

**Files:**
- Modify: `server/money.py` and `server/weather.py` `generate_problem` — before falling through to the built-in template for a requested operation, check `data/template_pool.json` for an unused entry with that `operation`; if found: `expected = forge_ops.OPS[e["op"]](*e["args"])`, mark `used: true` (write the file back), return `{"prompt": e["prompt"], "expected": float(expected), "parameters": {"operation": e["operation"], "source": "forge", "forge_id": e["id"], ...}}`. Pool file missing/empty → behave exactly as today.
- Test: `tests/test_money.py` (a tmp pool file fixture: entry consumed once, then built-in template resumes).

- [ ] **Steps:** failing test → implement → green → **Commit:** `feat(generator): grounded generators draw from forge pool`

---

## Final verification checklist (manual, after all stages)

1. `uv run pytest tests/ -q` — all green.
2. `uv run python tools/backfill_srs.py` then `uv run python tools/backfill_feedback_codes.py` on the real DB.
3. `uv run python tools/taxonomy_v2.py` — report shows carry/borrow-column families with display names and the 1200ms primitive target.
4. Start the server, run an 8-question drill: expect ≥1 bootstrap slot (division or an untested times-table), 2–3 grounded problems, no `category_share`, no problem repeated from the previous 15 attempts.
5. Answer one problem wrong on purpose → post-session review shows a strategy tip whose stated answer matches the expected answer.
6. Tap `too easy` on a review row → `item_state.due_at` for that key jumps weeks out (`sqlite3 data/feynman.db "SELECT item_key, datetime(due_at,'unixepoch') FROM item_state ORDER BY updated_at DESC LIMIT 3;"`).
7. Edit `suppressions.yaml` while the server runs → next generation respects it (no restart).
8. `uv run python tools/template_forge.py --dry-run` with `OPENAI_API_KEY` set → prints accepted candidates, writes nothing; without the key → clean error message, exit 1.
