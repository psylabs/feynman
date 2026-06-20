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

import math
import random
import statistics
import time
from typing import Callable

from server import diagnosis


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


FOUNDATION_SKILLS = {"addition", "subtraction", "multiplication", "division"}
GROUNDED_SKILLS = {"money_arithmetic", "weather_math"}


def build_session_plan(
    storage,
    user_id: str,
    length: int,
    emit: Callable,
) -> list[dict]:
    """Pre-compute a coherent session with the foundation/grounded split.

    Allocation: ~50% grounded (money + weather), the rest foundation theme
    facts plus retention. Foundation themes are picked from `drill_priorities`
    filtered to foundation skills.

    Returns a list of slot dicts in playback order (same shape as `pick_drill`
    plus `fact_key` and `role`). Empty list = cold start; caller should fall
    back to live `pick_drill` per question.
    """
    attempts = storage.all_attempts_for_user(user_id, limit=300)
    fact_stats = diagnosis.compute_fact_stats(attempts)
    skill_targets = {}
    available_skills = set(storage.all_skill_ids())
    for sid in available_skills:
        skill = storage.get_skill(sid)
        if skill:
            skill_targets[sid] = skill["target_latency_ms"]

    reg_keys = {r["fact_key"] for r in diagnosis.recent_regressions(attempts)}
    priorities = diagnosis.drill_priorities(
        fact_stats, skill_targets, min_attempts=2, limit=20,
        regression_keys=reg_keys,
    )

    retention_pool = diagnosis.mastered_for_retention(fact_stats, skill_targets)
    grounded_n = length // 2
    retention_target = 2 if length >= 12 else (1 if length >= 8 else 0)
    retention_n = min(retention_target, len(retention_pool))
    foundation_n = max(1, length - grounded_n - retention_n)

    foundation_priorities = [p for p in priorities if p["skill_id"] in FOUNDATION_SKILLS]
    if not foundation_priorities and not priorities:
        # Cold start. Live pick_drill handles per-question fallback; we still
        # seed grounded slots if any grounded skills exist so the user gets
        # the 50/50 mix from day one.
        grounded_slots = _build_grounded_slots(storage, user_id, available_skills, grounded_n, skill_targets)
        if not grounded_slots:
            emit("scheduler.session_plan", themes=[], slots=[], note="cold start")
            return []
        emit(
            "scheduler.session_plan",
            length=length,
            themes=[],
            grounded_count=len(grounded_slots),
            note="cold start; grounded-only seed",
            slots=[{"fact": s["fact_key"], "role": s["role"]} for s in grounded_slots],
        )
        return _spread_duplicates_with_alternation(grounded_slots)

    requested_themes = max(1, min(3, foundation_n // 2 or 1))
    theme_pool = foundation_priorities or priorities
    themes = _select_diverse_themes(theme_pool, requested_themes)
    num_themes = len(themes) or 1

    related_target = 1 if foundation_n >= 5 else 0
    related_slots: list[dict] = []
    for i in range(related_target):
        theme = themes[i % num_themes]
        rel_key = _pick_related(theme["fact_key"])
        if not rel_key:
            continue
        rel_skill = _skill_from_key(rel_key) or theme["skill_id"]
        related_slots.append(_slot_from_key(
            rel_key, rel_skill, "related",
            target_ms=skill_targets.get(rel_skill),
            reason=f"related to {theme['display']}",
        ))

    theme_total = max(0, foundation_n - len(related_slots))
    base_reps = theme_total // num_themes
    extra = theme_total % num_themes

    foundation_slots: list[dict] = []
    for i, t in enumerate(themes):
        reps = base_reps + (1 if i < extra else 0)
        for _ in range(reps):
            foundation_slots.append(_slot_from_key(
                t["fact_key"], t["skill_id"], "theme",
                display=t["display"],
                reason=f"slow: {t['display']} ({t['median_latency_ms']}ms, target {t['target_ms']}ms)",
                target_ms=t["target_ms"],
                diagnosis_median_latency_ms=t["median_latency_ms"],
                diagnosis_accuracy=t["accuracy"],
                diagnosis_n=t["n"],
                diagnosis_gap_ratio=t["gap_ratio"],
            ))
    foundation_slots.extend(related_slots)

    grounded_slots = _build_grounded_slots(storage, user_id, available_skills, grounded_n, skill_targets)

    retention_slots: list[dict] = []
    for i in range(retention_n):
        m = retention_pool[i]
        retention_slots.append(_slot_from_key(
            m["fact_key"], m["skill_id"], "retention",
            display=m["display"],
            target_ms=m.get("target_ms"),
            diagnosis_median_latency_ms=m.get("median_latency_ms"),
            diagnosis_accuracy=m.get("accuracy"),
            diagnosis_n=m.get("n"),
            reason=f"retention check: {m['display']} (last seen {m['days_since_seen']}d ago)",
        ))

    slots = foundation_slots + grounded_slots + retention_slots
    slots = _spread_duplicates_with_alternation(slots)

    emit(
        "scheduler.session_plan",
        length=length,
        themes=[
            {"fact": t["fact_key"], "display": t["display"],
             "priority": t["priority"], "regressed": t.get("regressed", False)}
            for t in themes
        ],
        retention_count=len(retention_slots),
        related_count=len(related_slots),
        grounded_count=len(grounded_slots),
        foundation_count=len(foundation_slots),
        slots=[{"fact": s["fact_key"], "role": s["role"]} for s in slots],
    )
    return slots


def _select_diverse_themes(priorities: list[dict], n: int) -> list[dict]:
    """Pick up to n themes, preferring one-per-skill before doubling up.

    Without this, a session whose top priorities are all `add:*` will be all
    addition. We walk the priority list twice: first pass picks at most one
    per skill (greedy by priority); second pass fills remaining slots from
    the leftovers.
    """
    chosen: list[dict] = []
    seen_skills: set[str] = set()
    leftovers: list[dict] = []
    for p in priorities:
        if len(chosen) >= n:
            break
        sid = p.get("skill_id")
        if sid in seen_skills:
            leftovers.append(p)
            continue
        chosen.append(p)
        seen_skills.add(sid)
    for p in leftovers:
        if len(chosen) >= n:
            break
        chosen.append(p)
    return chosen


_GROUNDED_OPS = {
    # tip + split_bill are enforced as dedicated slots in _build_grounded_slots,
    # so the random fill rotation covers the *other* money problems for variety.
    "money_arithmetic": ("charge_total", "category_difference", "category_share"),
    "weather_math": ("temp_delta", "daily_range", "f_to_c_approx", "wind_delta"),
}

# split-the-bill advances from 3-5 people to 6-8 once the user is fluent at the
# base set. Mirrors the diagnosis "mastered" bar (accuracy + speed, enough n).
SPLIT_BILL_BASE_MAX = 5
SPLIT_BILL_ADVANCED_MAX = 8
_SPLIT_UNLOCK_MIN_ATTEMPTS = 6
_SPLIT_UNLOCK_MIN_ACCURACY = 0.9


def _split_bill_max_party(storage, user_id: str, skill_targets: dict) -> int:
    """Return the largest split-the-bill party size the user has unlocked.

    Advanced divisors (6-8) unlock once they've split among 3-5 people enough
    times, accurately, and at or under the money latency target. Until then it
    stays capped at SPLIT_BILL_BASE_MAX (5 people)."""
    attempts = storage.all_attempts_for_user(user_id, limit=300)
    base = [
        a for a in attempts
        if a.get("skill_id") == "money_arithmetic"
        and (a.get("parameters") or {}).get("operation") == "split_bill"
        and (a.get("parameters") or {}).get("people") in (3, 4, 5)
    ]
    if len(base) < _SPLIT_UNLOCK_MIN_ATTEMPTS:
        return SPLIT_BILL_BASE_MAX
    accuracy = sum(1 for a in base if a.get("correct")) / len(base)
    if accuracy < _SPLIT_UNLOCK_MIN_ACCURACY:
        return SPLIT_BILL_BASE_MAX
    target_ms = skill_targets.get("money_arithmetic")
    lat = [a["resolution_latency_ms"] for a in base if a.get("resolution_latency_ms")]
    if target_ms and lat and statistics.median(lat) > target_ms:
        return SPLIT_BILL_BASE_MAX
    return SPLIT_BILL_ADVANCED_MAX


def _build_grounded_slots(
    storage,
    user_id: str,
    available_skills: set[str],
    n: int,
    skill_targets: dict,
) -> list[dict]:
    """Return up to `n` grounded slots split between weather and money,
    weighted toward whichever skill is more under-sampled by the user."""
    if n <= 0:
        return []
    grounded = [s for s in GROUNDED_SKILLS if s in available_skills]
    if not grounded:
        return []
    counts = {sid: storage.skill_attempt_count(user_id, sid) for sid in grounded}
    slots: list[dict] = []
    # Enforce two finance staples up front: a 15% tip and a split-the-bill.
    if "money_arithmetic" in grounded:
        max_party = _split_bill_max_party(storage, user_id, skill_targets)
        money_target = skill_targets.get("money_arithmetic")
        for fact_key, reason in (
            ("money:restaurant_tip_15", "grounded: restaurant tip (15%)"),
            ("money:split_bill", "grounded: split the bill"),
        ):
            if len(slots) >= n:
                break
            slot = _slot_from_key(
                fact_key, "money_arithmetic", "grounded",
                target_ms=money_target, reason=reason,
            )
            if fact_key == "money:split_bill" and isinstance(slot["target_fact"], dict):
                slot["target_fact"]["max_party"] = max_party
            slots.append(slot)
            counts["money_arithmetic"] += 1
    for _ in range(n - len(slots)):
        sid = min(counts, key=lambda s: counts[s])
        op = random.choice(_GROUNDED_OPS.get(sid, ("",)))
        prefix = _GROUNDED_PREFIX.get(sid, sid)
        fact_key = f"{prefix}:{op}" if op else sid
        slots.append(_slot_from_key(
            fact_key, sid, "grounded",
            target_ms=skill_targets.get(sid),
            reason=f"grounded: {sid.replace('_', ' ')}",
        ))
        counts[sid] += 1
    return slots


_GROUNDED_PREFIX = {
    "money_arithmetic": "money",
    "weather_math": "weather",
}


def _slot_from_key(
    fact_key: str,
    skill_id: str,
    role: str,
    display: str | None = None,
    reason: str | None = None,
    target_ms: int | None = None,
    diagnosis_median_latency_ms: int | None = None,
    diagnosis_accuracy: float | None = None,
    diagnosis_n: int | None = None,
    diagnosis_gap_ratio: float | None = None,
) -> dict:
    return {
        "skill_id": skill_id,
        "target_fact": _fact_key_to_target(fact_key),
        "level": _infer_level_from_key(fact_key),
        "fact_key": fact_key,
        "display": display or diagnosis.fact_display(fact_key),
        "role": role,
        "reason": reason or f"{role}: {display or fact_key}",
        "target_ms": target_ms,
        "diagnosis_median_latency_ms": diagnosis_median_latency_ms,
        "diagnosis_accuracy": diagnosis_accuracy,
        "diagnosis_n": diagnosis_n,
        "diagnosis_gap_ratio": diagnosis_gap_ratio,
    }


def _skill_from_key(key: str) -> str | None:
    if key.startswith("mul:"):
        return "multiplication"
    if key.startswith("div:"):
        return "division"
    if key.startswith("add:"):
        return "addition"
    if key.startswith("sub:"):
        return "subtraction"
    if key.startswith("pct:"):
        return "percent_of"
    if key.startswith("money:"):
        return "money_arithmetic"
    if key.startswith("weather:"):
        return "weather_math"
    return None


def _pick_related(key: str) -> str | None:
    """Return one related fact key in the same family, or None."""
    cands = _related_keys(key)
    return random.choice(cands) if cands else None


def _related_keys(key: str) -> list[str]:
    if key.startswith("mul:"):
        try:
            a, b = (int(x) for x in key[4:].split("x"))
        except ValueError:
            return []
        out: list[str] = []
        for x, y in [(a, a), (b, b), (a, b + 1), (a, max(2, b - 1)),
                     (a + 1, b), (max(2, a - 1), b)]:
            if not (2 <= x <= 12 and 2 <= y <= 12):
                continue
            lo, hi = sorted([x, y])
            k = f"mul:{lo}x{hi}"
            if k != key and k not in out:
                out.append(k)
        return out
    if key.startswith("div:"):
        try:
            a, b = (int(x) for x in key[4:].split("x"))
        except ValueError:
            return []
        out = []
        for x, y in [(a, a), (b, b), (a, b + 1), (a, max(2, b - 1)),
                     (a + 1, b), (max(2, a - 1), b)]:
            if not (2 <= x <= 12 and 2 <= y <= 12):
                continue
            lo, hi = sorted([x, y])
            k = f"div:{lo}x{hi}"
            if k != key and k not in out:
                out.append(k)
        return out
    if key.startswith("add:") or key.startswith("sub:"):
        prefix = key[:4]
        rest = key[4:]
        try:
            pattern, tag = rest.rsplit(":", 1)
        except ValueError:
            return []
        # Skip 3d patterns — old data may still surface them but we don't drill there anymore
        if "3d" in pattern:
            return []
        carry_tag = "c" if prefix == "add:" else "b"
        flipped = "n" if tag in ("c", "b") else carry_tag
        return [f"{prefix}{pattern}:{flipped}"]
    if key.startswith("pct:"):
        try:
            pct = int(key[4:])
        except ValueError:
            return []
        return [f"pct:{p}" for p in (10, 15, 20, 25, 50, 75) if p != pct]
    return []


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
