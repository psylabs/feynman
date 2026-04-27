"""Scheduler: picks the next skill to drill, and within a skill the level.

Skill choice: priority = (1 - mastery) × recency_factor × small jitter; top-K
weighted sample so it doesn't feel deterministic.

Level choice: walks per-(skill, level) recent accuracy from L3 down to L1 and
picks the highest level the user has not yet mastered (≥0.85 with at least 2
attempts). Once mastered, advances to the next level up. This is the
"edge of ability" rule applied within a skill.
"""

import collections
import math
import random
import time
from typing import Callable


def pick_skill(skill_states: list[dict], emit: Callable) -> dict:
    if not skill_states:
        raise ValueError("no skills registered")

    now = time.time()
    table = []
    for s in skill_states:
        mastery = s.get("mastery") if s.get("mastery") is not None else 0.5
        last_seen = s.get("last_seen_at") or 0.0
        days_since = (now - last_seen) / 86400.0 if last_seen else 999.0

        # recency: ~0.4 just after seeing, climbs to ~1.0 over a few days
        recency = 1.0 - 0.6 * math.exp(-days_since / 1.5)
        jitter = 1.0 + random.uniform(-0.1, 0.1)
        priority = (1.0 - mastery) * recency * jitter

        table.append(
            {
                "skill_id": s["skill_id"],
                "mastery": round(mastery, 3),
                "days_since_last_seen": round(days_since, 3),
                "recency_factor": round(recency, 3),
                "priority": round(priority, 4),
            }
        )

    table.sort(key=lambda x: x["priority"], reverse=True)

    top_k = table[: min(3, len(table))]
    weights = [max(0.01, p["priority"]) for p in top_k]
    chosen = random.choices(top_k, weights=weights, k=1)[0]

    emit(
        "scheduler.chose",
        chosen=chosen["skill_id"],
        priorities=table,
    )

    # find the original mastery value (unrounded) from the input list
    raw_mastery = next(
        (s.get("mastery") if s.get("mastery") is not None else 0.5
         for s in skill_states if s["skill_id"] == chosen["skill_id"]),
        0.5,
    )

    return {"skill_id": chosen["skill_id"], "mastery": raw_mastery}


_MASTERY_THRESHOLD = 0.85
_MIN_ATTEMPTS_TO_JUDGE = 2


def pick_level(
    storage,
    user_id: str,
    skill_id: str,
    emit: Callable | None = None,
) -> int:
    """Pick the next difficulty level (1-3) for a drill on `skill_id`.

    Walks recent attempts grouped by level. Picks the highest level the user
    hasn't yet mastered; advances one level once a level is solid.
    """
    attempts = storage.recent_attempts_for_skill(user_id, skill_id, limit=30)
    by_level: dict[int, list[bool]] = collections.defaultdict(list)
    for a in attempts:
        if a.get("skipped"):
            continue
        params = a.get("parameters") or {}
        if isinstance(params, dict):
            lvl = params.get("level")
            if lvl in (1, 2, 3):
                by_level[lvl].append(bool(a.get("correct")))

    accs: dict[int, float] = {}
    for lvl, results in by_level.items():
        if results:
            accs[lvl] = sum(results) / len(results)

    if not accs:
        chosen = 1
    else:
        # Highest level still being learned (acc < threshold with enough data)
        chosen = None
        for lvl in (3, 2, 1):
            if lvl in accs and len(by_level[lvl]) >= _MIN_ATTEMPTS_TO_JUDGE \
                    and accs[lvl] < _MASTERY_THRESHOLD:
                chosen = lvl
                break
        # Otherwise advance one level above the highest mastered one we've seen
        if chosen is None:
            highest_seen = max(accs.keys())
            chosen = min(3, highest_seen + 1) if accs[highest_seen] >= _MASTERY_THRESHOLD else highest_seen

    if emit:
        emit(
            "scheduler.level_picked",
            skill_id=skill_id,
            chosen=chosen,
            accuracies={str(k): round(v, 3) for k, v in accs.items()},
            n_per_level={str(k): len(v) for k, v in by_level.items()},
        )
    return chosen
