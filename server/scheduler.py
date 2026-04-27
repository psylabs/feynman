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

    priorities = diagnosis.drill_priorities(fact_stats, skill_targets, min_attempts=2, limit=20)

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
