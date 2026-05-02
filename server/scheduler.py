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


def build_session_plan(
    storage,
    user_id: str,
    length: int,
    emit: Callable,
) -> list[dict]:
    """Pre-compute a coherent session: 2–3 theme facts repeated, plus related
    and retention picks.

    Returns a list of slot dicts in playback order (same shape as `pick_drill`
    plus `fact_key` and `role`). Empty list = not enough data; caller should
    fall back to live `pick_drill` per question.
    """
    attempts = storage.all_attempts_for_user(user_id, limit=300)
    fact_stats = diagnosis.compute_fact_stats(attempts)
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
        emit("scheduler.session_plan", themes=[], slots=[], note="cold start")
        return []

    requested_themes = max(1, min(3, length // 4))
    themes = _select_diverse_themes(priorities, requested_themes)
    num_themes = len(themes)

    retention_pool = diagnosis.mastered_for_retention(fact_stats, skill_targets)
    retention_n = min(2, len(retention_pool), 1 if length >= 6 else 0) if length >= 6 else 0
    if length >= 12 and len(retention_pool) >= 2:
        retention_n = 2

    related_n = 1 if length >= 5 else 0
    if length >= 10:
        related_n = 2

    theme_total = max(0, length - retention_n - related_n)
    base_reps = theme_total // num_themes
    extra = theme_total % num_themes

    slots: list[dict] = []
    for i, t in enumerate(themes):
        reps = base_reps + (1 if i < extra else 0)
        for _ in range(reps):
            slots.append(_slot_from_key(t["fact_key"], t["skill_id"], "theme",
                                         display=t["display"],
                                         reason=f"slow: {t['display']} ({t['median_latency_ms']}ms, target {t['target_ms']}ms)"))

    for i in range(related_n):
        theme = themes[i % num_themes]
        rel_key = _pick_related(theme["fact_key"])
        if rel_key:
            slots.append(_slot_from_key(rel_key, _skill_from_key(rel_key) or theme["skill_id"],
                                         "related",
                                         reason=f"related to {theme['display']}"))

    for i in range(retention_n):
        m = retention_pool[i]
        slots.append(_slot_from_key(m["fact_key"], m["skill_id"], "retention",
                                     display=m["display"],
                                     reason=f"retention check: {m['display']} (last seen {m['days_since_seen']}d ago)"))

    slots = _spread_duplicates(slots)

    emit(
        "scheduler.session_plan",
        length=length,
        themes=[
            {"fact": t["fact_key"], "display": t["display"],
             "priority": t["priority"], "regressed": t.get("regressed", False)}
            for t in themes
        ],
        retention_count=retention_n,
        related_count=related_n,
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


def _slot_from_key(
    fact_key: str,
    skill_id: str,
    role: str,
    display: str | None = None,
    reason: str | None = None,
) -> dict:
    return {
        "skill_id": skill_id,
        "target_fact": _fact_key_to_target(fact_key),
        "level": _infer_level_from_key(fact_key),
        "fact_key": fact_key,
        "display": display or diagnosis.fact_display(fact_key),
        "role": role,
        "reason": reason or f"{role}: {display or fact_key}",
    }


def _skill_from_key(key: str) -> str | None:
    if key.startswith("mul:"):
        return "multiplication"
    if key.startswith("add:"):
        return "addition"
    if key.startswith("sub:"):
        return "subtraction"
    if key.startswith("pct:"):
        return "percent_of"
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
    if key.startswith("add:") or key.startswith("sub:"):
        prefix = key[:4]
        rest = key[4:]
        try:
            pattern, tag = rest.rsplit(":", 1)
        except ValueError:
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
    # For add/sub/pct patterns, the generator handles the randomness
    # within the pattern. We pass hints.
    if key.startswith("add:") or key.startswith("sub:"):
        return _parse_arithmetic_pattern(key)
    if key.startswith("pct:"):
        pct = int(key[4:])
        return {"percentage": pct}
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
        if a <= 5 or b <= 5 or a == 10 or b == 10:
            return 1
        if a <= 12 and b <= 12:
            return 2
        return 3
    if key.startswith("add:") or key.startswith("sub:"):
        # Count total digits involved
        rest = key[4:] if key.startswith("add:") else key[4:]
        if "3d" in rest:
            return 3
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
    return 2
