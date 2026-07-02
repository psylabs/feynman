"""Scheduler: diagnosis-first drill selection.

Instead of picking a skill then a level, we:
1. Pull all recent attempts for the user.
2. Compute per-fact stats via the diagnosis engine.
3. Rank facts by priority (slowest, least accurate, most room for improvement).
4. Pick a high-priority target, with some weighted randomness so it doesn't
   feel deterministic.

The scheduler can return either:
  - A specific fact to drill (e.g., 7x8 for multiplication)
  - A pattern to drill (e.g., 2-digit addition with carry)

Lower-level fluency influences priority weighting (soft prerequisite), but
nothing is hard-locked.
"""

import json
import math
import random
import time
from typing import Callable

from server import diagnosis, family_stats, taxonomy


def pick_drill(
    storage,
    user_id: str,
    emit: Callable,
) -> dict:
    """Pick the next problem to drill.

    Returns {skill_id, target_fact: {a, b, ...} | None, level, reason}.
    target_fact is set when we want a specific problem; None means
    generate a random problem matching the skill/level.
    """
    attempts = storage.all_attempts_for_user(user_id, limit=300)
    fact_stats = diagnosis.compute_fact_stats(attempts)

    # Build target latency map from skills
    skill_targets = {}
    for sid in storage.all_skill_ids():
        skill = storage.get_skill(sid)
        if skill:
            skill_targets[sid] = skill["target_latency_ms"]

    reg_keys = {r["fact_key"] for r in diagnosis.recent_regressions(attempts)}
    priorities = diagnosis.drill_priorities(
        fact_stats, skill_targets, min_attempts=2, limit=20,
        regression_keys=reg_keys,
    )

    if not priorities:
        # Cold start or very few attempts: fall back to skill-level selection
        return _cold_start_pick(storage, user_id, emit)

    # Weighted sample from top priorities (not just the single worst fact)
    top_k = priorities[:min(8, len(priorities))]
    weights = [max(0.1, p["priority"]) for p in top_k]
    chosen = random.choices(top_k, weights=weights, k=1)[0]

    # Determine what to generate
    fact_key = chosen["fact_key"]
    skill_id = chosen["skill_id"]
    target_fact = _fact_key_to_target(fact_key)
    level = _infer_level_from_key(fact_key)

    emit(
        "scheduler.diagnosis_pick",
        chosen_fact=fact_key,
        display=chosen["display"],
        priority=chosen["priority"],
        gap_ratio=chosen["gap_ratio"],
        median_ms=chosen["median_latency_ms"],
        target_ms=chosen["target_ms"],
        accuracy=chosen["accuracy"],
        top_priorities=[
            {"fact": p["fact_key"], "priority": p["priority"]}
            for p in top_k
        ],
    )

    return {
        "skill_id": skill_id,
        "target_fact": target_fact,
        "level": level,
        "reason": f"slow: {chosen['display']} ({chosen['median_latency_ms']}ms, target {chosen['target_ms']}ms)",
    }


# ---------------------------------------------------------------------------
# Taxonomy-driven session planning (Task 3.2)
#
# Selection order: due reviews first, then one bootstrap slot while primitive
# foundations are still untested, then weak-family drills, then a cold-start
# fallback for brand-new users. Eligible non-grounded slots are then "skinned"
# into grounded money/weather ops until the session holds a healthy share of
# grounded practice.
# ---------------------------------------------------------------------------

HEALTHY_GROUNDED_SHARE = 0.35   # "healthy amount", not a quota (user decision 2026-07-01)
RECENT_KEY_WINDOW = 15          # last N attempt keys excluded unless overdue > 1 day
BOOTSTRAP_ROLE = "bootstrap"

GROUNDED_SKILL_IDS = {"money_arithmetic", "weather_math"}

# Skin map: family-prefix -> grounded ops that exercise the same skeleton skill.
SKINS = {
    "sub.2d": [("weather_math", "temp_delta"), ("weather_math", "daily_range")],
    "sub.3d": [("money_arithmetic", "category_difference")],
    "add.2d": [("money_arithmetic", "charge_total")],
    "add.3d": [("money_arithmetic", "charge_total")],
    "pct":    [("money_arithmetic", "restaurant_tip_15"), ("money_arithmetic", "category_amount")],
    "div":    [("money_arithmetic", "split_bill")],
}

# Primitive foundations we guarantee at least one look at per session until the
# user has ≥5 attempts in each. Ordered so bootstrap rotation prefers the
# earliest-listed among equally-untested families (see _pick_bootstrap_family).
BOOTSTRAP_FAMILIES = (
    [f"mul.x{n}" for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 20)]
    + [f"div.x{n}" for n in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 20)]
    + ["add.bridge10", "sub.borrow20"]
)

_PREFIX_TO_SKILL = {
    "add": "addition",
    "sub": "subtraction",
    "mul": "multiplication",
    "div": "division",
    "money": "money_arithmetic",
    "weather": "weather_math",
    "pct": "percent_of",
}


def build_session_plan(
    storage,
    user_id: str,
    length: int,
    emit: Callable,
) -> list[dict]:
    """Compute a due-first, need-driven session plan.

    Returns a list of slot dicts in playback order. Same shape as the historic
    plan slots (``skill_id``, ``target_fact``, ``level``, ``fact_key``,
    ``display``, ``role``, ``reason``, ``target_ms``, ``diagnosis_*``) plus two
    new optional keys: ``family`` (taxonomy family the slot drills) and
    ``covers`` (skeleton item_key a skinned grounded slot also credits).

    Cold-start users (no due items, no weak families, everything untested) get a
    bootstrap slot plus cold-start fills; the orchestrator still falls back to
    live ``pick_drill`` if the plan runs short.
    """
    now = time.time()
    attempts = storage.all_attempts_for_user(user_id, limit=500)

    skill_targets: dict[str, int] = {}
    for sid in storage.all_skill_ids():
        sk = storage.get_skill(sid)
        if sk:
            skill_targets[sid] = sk["target_latency_ms"]

    recent = _recent_item_keys(attempts)
    # Task 4.2 will add these storage methods; until then they resolve to set().
    cooldown = _optional_key_set(storage, "active_cooldowns", user_id, now)
    excluded = _optional_key_set(storage, "excluded_keys", user_id)
    blocked_unless_overdue = recent | cooldown

    slots: list[dict] = []
    seen_keys: set[str] = set()

    # 1. Due reviews, most overdue first. Items >1 day overdue bypass the
    #    recency/cooldown exclusion; excluded_keys always wins.
    for item in storage.due_items(user_id, now, limit=length * 2):
        if len(slots) >= length:
            break
        key = item["item_key"]
        if key in excluded or key in seen_keys:
            continue
        overdue_days = (now - item.get("due_at", now)) / 86400
        if key in blocked_unless_overdue and overdue_days < 1:
            continue
        slots.append(_slot_for_item(item, attempts, skill_targets))
        seen_keys.add(key)

    stats = family_stats.family_stats(attempts)

    # 2. One bootstrap slot per session while any foundation family is untested.
    if len(slots) < length:
        fam = _pick_bootstrap_family(stats)
        if fam is not None:
            slots.append(_bootstrap_slot(fam, attempts, skill_targets))

    # 3. Weak-family drills fill the rest (weak_families order, round-robin).
    #    excluded_keys always wins; cooldown/recency also blocked (no overdue bypass
    #    since weak slots aren't due items). Families and item keys live in
    #    different namespaces, so translate via _family_blocked before comparing.
    weak = [
        f for f in family_stats.weak_families(stats, skill_targets, limit=10)
        if not _family_blocked(f, excluded)
        and not _family_blocked(f, blocked_unless_overdue)
    ]
    if weak:
        i = 0
        while len(slots) < length and i <= length * 4:
            f = weak[i % len(weak)]
            fam_stat = stats.get(f, {})
            tier = fam_stat.get("tier") or ("primitive" if _is_primitive_family(f) else "compound")
            slots.append(_weak_family_slot(f, attempts, skill_targets, tier=tier))
            i += 1

    # 4. Cold-start fallback for brand-new users: fill any remaining slots.
    while len(slots) < length:
        pick = _cold_start_pick(storage, user_id, emit)
        slots.append(_make_slot(
            pick["skill_id"], pick["skill_id"], "exploration",
            pick.get("target_fact"), pick["level"],
            reason=pick.get("reason", "cold start"),
        ))

    apply_skins(slots, length, excluded=excluded, cooldown=cooldown)
    plan = _spread_duplicates_with_alternation(slots)

    emit(
        "scheduler.session_plan",
        length=length,
        due_count=sum(1 for s in slots if s["role"].startswith("due")),
        bootstrap_count=sum(1 for s in slots if s["role"].startswith(BOOTSTRAP_ROLE)),
        weak_count=sum(1 for s in slots if s["role"].startswith("weak")),
        grounded_count=sum(1 for s in slots if s["skill_id"] in GROUNDED_SKILL_IDS),
        slots=[{"fact": s["fact_key"], "role": s["role"]} for s in plan],
    )
    return plan


def apply_skins(
    slots: list[dict],
    length: int,
    excluded: set | None = None,
    cooldown: set | None = None,
) -> list[dict]:
    """Render eligible slots through grounded generators until the session holds
    ceil(length * HEALTHY_GROUNDED_SHARE) grounded slots (or eligibility runs
    out). A skinned slot keeps ``covers=<original item_key>`` so grading credits
    both the grounded op and the underlying skeleton item. Already-grounded slots
    are never re-skinned.

    ``excluded``/``cooldown`` are sets of item KEYS (``money:X``/``weather:X``);
    a skin candidate whose grounded op key falls in either set is skipped. Recent
    keys are NOT excluded here — skins cover a skeleton that was already
    recency-checked when its weak/due slot was chosen."""
    blocked = (excluded or set()) | (cooldown or set())
    target = math.ceil(length * HEALTHY_GROUNDED_SHARE)
    grounded = sum(1 for s in slots if s["skill_id"] in GROUNDED_SKILL_IDS)
    for s in slots:
        if grounded >= target:
            break
        if s["skill_id"] in GROUNDED_SKILL_IDS:
            continue
        if s.get("tier") == "primitive":
            continue
        fam = s.get("family") or ""
        prefix = next((p for p in SKINS if fam.startswith(p)), None)
        if prefix is None:
            continue
        candidates = [
            (skill_id, op)
            for (skill_id, op) in SKINS[prefix]
            if f"{'money' if skill_id == 'money_arithmetic' else 'weather'}:{op}"
            not in blocked
        ]
        if not candidates:
            continue
        skill_id, op = random.choice(candidates)
        word = "money" if skill_id == "money_arithmetic" else "weather"
        s.update(
            skill_id=skill_id,
            covers=s["fact_key"],
            fact_key=f"{word}:{op}",
            target_fact={"operation": op},
            role=s["role"] + "+skin",
            display=f"{word}: {op.replace('_', ' ')}",
            target_ms=None,
        )
        grounded += 1
    return slots


# ---- slot construction -----------------------------------------------------


def _make_slot(
    skill_id: str,
    fact_key: str,
    role: str,
    target_fact: dict | None,
    level: int,
    *,
    family: str | None = None,
    covers: str | None = None,
    display: str | None = None,
    reason: str | None = None,
    target_ms: int | None = None,
    tier: str | None = None,
) -> dict:
    """Build a plan slot with the full shape the orchestrator/analysis consume."""
    if display is None:
        display = taxonomy.family_display(family) if family else fact_key
    return {
        "skill_id": skill_id,
        "target_fact": target_fact,
        "level": level,
        "fact_key": fact_key,
        "display": display,
        "role": role,
        "reason": reason or f"{role}: {display}",
        "target_ms": target_ms,
        "diagnosis_median_latency_ms": None,
        "diagnosis_accuracy": None,
        "diagnosis_n": None,
        "diagnosis_gap_ratio": None,
        "family": family,
        "covers": covers,
        "tier": tier,
    }


def _slot_for_item(item: dict, attempts: list[dict], skill_targets: dict) -> dict:
    """A due-review slot for one item_state row. Primitive facts pin the exact
    fact via the generator target; compound families generate at family level."""
    key = item["item_key"]
    family = item.get("family") or (key.split(":", 1)[0] if ":" in key else key)
    skill_id = _skill_for_item_key(key)
    target_fact = _target_from_item_key(key)
    level = family_stats.family_level(attempts, family)
    is_primitive = item.get("tier") == "primitive"
    target_ms = (
        taxonomy.PRIMITIVE_ONSET_TARGET_MS if is_primitive
        else skill_targets.get(skill_id)
    )
    return _make_slot(
        skill_id, key, "due", target_fact, level,
        family=family, target_ms=target_ms, tier=item.get("tier"),
        reason=f"due review: {taxonomy.family_display(family)}",
    )


def _weak_family_slot(
    family: str, attempts: list[dict], skill_targets: dict, tier: str | None = None
) -> dict:
    """A drill slot for a weak family. Generated at the family's evidence-based
    level; no exact target (family-level variety)."""
    skill_id = _skill_for_item_key(family)
    level = family_stats.family_level(attempts, family)
    if tier is None:
        tier = "primitive" if _is_primitive_family(family) else "compound"
    target_ms = (
        taxonomy.PRIMITIVE_ONSET_TARGET_MS if tier == "primitive"
        else skill_targets.get(skill_id)
    )
    return _make_slot(
        skill_id, family, "weak", None, level,
        family=family, target_ms=target_ms, tier=tier,
        reason=f"needs work: {taxonomy.family_display(family)}",
    )


def _bootstrap_slot(family: str, attempts: list[dict], skill_targets: dict) -> dict:
    """A first-look slot for an untested foundation family."""
    skill_id, fact_key, target_fact = _bootstrap_fact(family)
    level = family_stats.family_level(attempts, family)
    return _make_slot(
        skill_id, fact_key, BOOTSTRAP_ROLE, target_fact, level,
        family=family, target_ms=taxonomy.PRIMITIVE_ONSET_TARGET_MS, tier="primitive",
        reason=f"first look: {taxonomy.family_display(family)}",
    )


def _bootstrap_fact(family: str) -> tuple[str, str, dict | None]:
    """Pick a concrete primitive fact representing a bootstrap family."""
    if family.startswith("mul.x"):
        n = int(family.removeprefix("mul.x"))
        b = random.randint(2, 12)
        lo, hi = sorted((n, b))
        return "multiplication", f"mul:{lo}x{hi}", {"a": n, "b": b}
    if family.startswith("div.x"):
        n = int(family.removeprefix("div.x"))
        b = random.randint(2, 12)
        lo, hi = sorted((n, b))
        return "division", f"div:{lo}x{hi}", {"a": n * b, "b": n}
    if family == "add.bridge10":
        a = random.randint(4, 9)
        b = random.randint(11 - a, min(9, 20 - a))
        lo, hi = sorted((a, b))
        return "addition", f"add:{lo}+{hi}", {"a": a, "b": b}
    if family == "sub.borrow20":
        a = random.choice([12, 13, 14, 15, 16, 17])
        b = random.randint(a % 10 + 1, 9)
        return "subtraction", f"sub:{a}-{b}", {"a": a, "b": b}
    # Unknown family: generate freely within the skill.
    return _skill_for_item_key(family), family, None


# ---- key/target translation ------------------------------------------------


def _skill_for_item_key(key: str) -> str:
    """Map an item_key / family to its originating skill_id."""
    prefix = key.split(":", 1)[0].split(".", 1)[0]
    return _PREFIX_TO_SKILL.get(prefix, prefix)


def _target_from_item_key(key: str) -> dict | None:
    """Translate a taxonomy item_key into generator target params.

    Primitive fact keys pin an exact problem:
        mul:6x7  -> {"a": 6, "b": 7}
        add:5+8  -> {"a": 5, "b": 8}
        sub:18-4 -> {"a": 18, "b": 4}
        div:7x8  -> {"a": 56, "b": 7}   (56 / 7 = 8; divisor/quotient form)
        pct:15   -> {"percentage": 15}
        money:split_bill -> {"operation": "split_bill"}
    Compound family keys (which use '.' separators, e.g. 'sub.3d-2d.bt') return
    None so the generator samples freely within the family.
    """
    if ":" not in key:
        return None
    prefix, rest = key.split(":", 1)
    try:
        if prefix == "mul":
            a, b = (int(x) for x in rest.split("x"))
            return {"a": a, "b": b}
        if prefix == "div":
            lo, hi = (int(x) for x in rest.split("x"))
            return {"a": lo * hi, "b": lo}
        if prefix == "add":
            a, b = (int(x) for x in rest.split("+"))
            return {"a": a, "b": b}
        if prefix == "sub":
            a, b = (int(x) for x in rest.split("-"))
            return {"a": a, "b": b}
        if prefix == "pct":
            return {"percentage": int(rest)}
    except ValueError:
        return None
    if prefix in ("money", "weather"):
        return {"operation": rest}
    return None


# ---- helpers ---------------------------------------------------------------


def _family_blocked(family: str, key_set: set) -> bool:
    """Return True if a weak-lane FAMILY should be blocked, given a set of item
    KEYS (cooldown / excluded / recent). Families and item keys live in
    different namespaces (``money.split_bill`` vs ``money:split_bill``,
    ``pct`` vs ``pct:15``), so a plain ``family in key_set`` test silently misses
    almost everything — only compound add/sub, where family == key, would match.

    Rules (implemented exactly):
      a. Exact match: ``family in key_set``. Covers compound add/sub families
         (family == key) and any family-grain entry such as the literal "pct".
      b. money./weather. families are single grounded ops (1:1 family<->key):
         map family -> key by replacing the first "." with ":".
      c. pct: only a literal "pct" entry blocks the family (handled by rule a).
         Per-percentage cooldowns (e.g. "pct:15") stay due-lane-only — a cooldown
         on one percentage must NOT block the whole pct family. Known coarseness.
      d. Primitive fact families (mul.xN, div.xN, add/sub within-20): rule (a)
         only. A cooldown on one fact (e.g. "mul:7x8") is honored by the due
         lane, not by blocking the entire mul.x8 weak family.
    """
    if family in key_set:  # rule a (also covers rule c's literal "pct" and rule d)
        return True
    if family.startswith("money.") or family.startswith("weather."):  # rule b
        return family.replace(".", ":", 1) in key_set
    # Rules c & d: pct:N and per-fact primitive cooldowns are due-lane-only; the
    # weak lane deliberately does not translate them into family-level blocks.
    return False


def _is_primitive_family(family: str) -> bool:
    return (
        family.startswith("mul.x") or family.startswith("div.x")
        or family in ("add.bridge10", "add.within20", "sub.borrow20", "sub.within20")
    )


def _recent_item_keys(attempts: list[dict]) -> set[str]:
    """Taxonomy keys of the last RECENT_KEY_WINDOW non-skipped attempts.
    attempts are newest-first (storage.all_attempts_for_user)."""
    keys: set[str] = set()
    seen = 0
    for a in attempts:
        if seen >= RECENT_KEY_WINDOW:
            break
        if a.get("skipped"):
            continue
        params = a.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                params = {}
        seen += 1
        taxon = taxonomy.classify(a.get("skill_id", ""), params)
        if taxon is not None:
            keys.add(taxon.key)
    return keys


def _pick_bootstrap_family(stats: dict[str, dict]) -> str | None:
    """The least-tested bootstrap family with <5 attempts, or None if all are
    covered. Ties break toward the earliest-listed family (stable sort)."""
    untested = [
        (stats.get(fam, {}).get("n", 0), idx, fam)
        for idx, fam in enumerate(BOOTSTRAP_FAMILIES)
        if stats.get(fam, {}).get("n", 0) < 5
    ]
    if not untested:
        return None
    untested.sort(key=lambda t: (t[0], t[1]))
    return untested[0][2]


def _optional_key_set(storage, method: str, *args) -> set:
    """Call an optional storage method, returning set() when it's absent.

    Task 4.2 adds ``active_cooldowns``/``excluded_keys``; until then this lets
    the scheduler run unchanged. Adding the methods later needs no edit here.
    """
    fn = getattr(storage, method, None)
    if fn is None:
        return set()
    try:
        return set(fn(*args))
    except Exception:  # pragma: no cover - defensive
        return set()


def _spread_duplicates(slots: list[dict]) -> list[dict]:
    """Shuffle while avoiding back-to-back identical fact_keys when possible."""
    if len(slots) <= 2:
        return slots
    shuffled = slots[:]
    random.shuffle(shuffled)
    for _ in range(8):
        bad = [i for i in range(1, len(shuffled))
               if shuffled[i]["fact_key"] == shuffled[i - 1]["fact_key"]]
        if not bad:
            break
        for i in bad:
            for j in range(len(shuffled)):
                if j in (i - 1, i, i + 1 if i + 1 < len(shuffled) else -1):
                    continue
                if shuffled[j]["fact_key"] == shuffled[i]["fact_key"]:
                    continue
                shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
                break
    return shuffled


def _spread_duplicates_with_alternation(slots: list[dict]) -> list[dict]:
    """Spread duplicates and try to break up adjacent grounded slots.

    Two passes: first dedupe by fact_key (existing behavior), then walk the
    list and swap any adjacent grounded pair with the nearest non-grounded
    slot. Cheap heuristic; not guaranteed alternation when grounded slots
    outnumber non-grounded.
    """
    out = _spread_duplicates(slots)
    for _ in range(4):
        bad = [i for i in range(1, len(out))
               if out[i].get("role") == "grounded" and out[i - 1].get("role") == "grounded"]
        if not bad:
            break
        moved = False
        for i in bad:
            for j in range(len(out)):
                if j in (i - 1, i):
                    continue
                if out[j].get("role") == "grounded":
                    continue
                # Avoid swapping into a position that creates a new grounded-pair
                if j > 0 and out[j - 1].get("role") == "grounded" and j != i + 1:
                    continue
                out[i], out[j] = out[j], out[i]
                moved = True
                break
            if moved:
                break
        if not moved:
            break
    return out


def _cold_start_pick(storage, user_id: str, emit: Callable) -> dict:
    """Fallback when we don't have enough data for diagnosis.

    Picks a random skill, random level. This only fires for the first
    few drills before the user has enough attempts.
    """
    skill_ids = storage.all_skill_ids()
    if not skill_ids:
        raise ValueError("no skills registered")

    # Prefer skills with fewer attempts
    counts = []
    for sid in skill_ids:
        n = storage.skill_attempt_count(user_id, sid)
        counts.append((sid, n))

    # Weight inversely by attempt count
    min_n = min(c for _, c in counts) if counts else 0
    weights = [max(1, 10 - (n - min_n)) for _, n in counts]
    chosen_sid = random.choices(
        [sid for sid, _ in counts], weights=weights, k=1
    )[0]

    # Start with L1, move up as attempts accumulate
    n = storage.skill_attempt_count(user_id, chosen_sid)
    level = 1 if n < 5 else (2 if n < 15 else 3)

    emit(
        "scheduler.cold_start",
        skill_id=chosen_sid,
        level=level,
        reason="not enough data for diagnosis",
    )

    return {
        "skill_id": chosen_sid,
        "target_fact": None,
        "level": level,
        "reason": "cold start",
    }


def _fact_key_to_target(key: str) -> dict | None:
    """Convert a fact key back to generator parameters.

    For multiplication facts (mul:6x7), returns specific operands.
    For patterns (add:2d+2d:c), returns None — generator picks randomly
    within that pattern.
    """
    if key.startswith("mul:"):
        parts = key[4:].split("x")
        if len(parts) == 2:
            a, b = int(parts[0]), int(parts[1])
            # Randomize order so it doesn't always say "6 times 7"
            if random.random() < 0.5:
                a, b = b, a
            return {"a": a, "b": b}
    if key.startswith("div:"):
        parts = key[4:].split("x")
        if len(parts) == 2:
            lo, hi = int(parts[0]), int(parts[1])
            # Two divisions share this family: (lo*hi)/lo and (lo*hi)/hi.
            # Pick either with equal probability.
            if random.random() < 0.5:
                divisor = lo
            else:
                divisor = hi
            return {"a": lo * hi, "b": divisor}
    # For add/sub/pct patterns, the generator handles the randomness
    # within the pattern. We pass hints.
    if key.startswith("add:") or key.startswith("sub:"):
        return _parse_arithmetic_pattern(key)
    if key.startswith("pct:"):
        pct = int(key[4:])
        return {"percentage": pct}
    if key.startswith("money:"):
        return {"operation": key[6:]}
    if key.startswith("weather:"):
        return {"operation": key[8:]}
    return None


def _parse_arithmetic_pattern(key: str) -> dict:
    """Parse add:2d+2d:c or sub:3d-2d:b into generator hints."""
    if key.startswith("add:"):
        rest = key[4:]
    elif key.startswith("sub:"):
        rest = key[4:]
    else:
        return {}

    pattern, tag = rest.rsplit(":", 1)
    # tag: c=carry, b=borrow, n=neither
    return {"pattern": pattern, "force_carry": tag == "c", "force_borrow": tag == "b"}


def _infer_level_from_key(key: str) -> int:
    """Infer difficulty level from a fact key for the generator."""
    if key.startswith("mul:"):
        parts = key[4:].split("x")
        a, b = int(parts[0]), int(parts[1])
        if a in (2, 5, 10, 11) or b in (2, 5, 10, 11):
            return 1
        if a in (12, 15, 20) or b in (12, 15, 20):
            return 3
        return 2
    if key.startswith("div:"):
        parts = key[4:].split("x")
        a, b = int(parts[0]), int(parts[1])
        # Mirror the multiplication tiering: ÷2/5/10 = L1, ÷12/15/20 = L3, rest L2
        if a in (2, 5, 10) or b in (2, 5, 10):
            return 1
        if a in (11, 12, 15, 20) or b in (11, 12, 15, 20):
            return 3
        return 2
    if key.startswith("add:") or key.startswith("sub:"):
        # Foundation lane caps everything to L1/L2; treat 3d (legacy) as L2 so
        # the generator's degraded 2d path runs.
        rest = key[4:]
        if "3d" in rest:
            return 2
        pattern, tag = rest.rsplit(":", 1)
        if tag in ("c", "b"):
            return 2
        return 1
    if key.startswith("pct:"):
        pct = int(key[4:])
        if pct in (10, 20, 50):
            return 1
        if pct in (15, 25):
            return 2
        return 3
    if key.startswith("weather:"):
        return 1
    if key.startswith("money:"):
        return 2
    return 2
