"""Recompute skill_state from the recent history of attempts."""

import statistics
import time
from typing import Callable


def update(
    storage,
    user_id: str,
    skill_id: str,
    target_latency_ms: int,
    emit: Callable,
) -> None:
    attempts = storage.recent_attempts_for_skill(user_id, skill_id, limit=10)
    counted = [a for a in attempts if not a.get("skipped")]

    if not counted:
        emit("mastery.skipped_update", skill_id=skill_id, reason="no_counted_attempts")
        return

    correctness = [bool(a.get("correct")) for a in counted]
    accuracy = sum(correctness) / len(correctness)

    latencies = [
        a["resolution_latency_ms"]
        for a in counted
        if a.get("resolution_latency_ms")
    ]
    median_latency = statistics.median(latencies) if latencies else None

    if median_latency and median_latency > 0:
        speed = max(0.0, min(1.0, target_latency_ms / median_latency))
    else:
        speed = 0.5

    mastery = 0.7 * accuracy + 0.3 * speed
    state = {
        "rolling_accuracy": accuracy,
        "median_latency_ms": int(median_latency) if median_latency else None,
        "mastery": mastery,
        "last_seen_at": time.time(),
        "attempt_count": storage.skill_attempt_count(user_id, skill_id),
    }
    storage.update_skill_state(user_id, skill_id, state)
    emit("mastery.updated", user_id=user_id, skill_id=skill_id, **state)
