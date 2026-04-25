"""Scheduler: picks the next skill to drill.

Priority is (1 - mastery) × recency_factor × small jitter. Top-K weighted sample
keeps it from feeling deterministic.
"""

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
