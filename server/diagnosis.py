"""Diagnosis engine: extracts fact keys, computes per-fact stats, identifies
the user's slowest facts and patterns from their attempt history.

This is the core of the "diagnosis-first" approach: instead of walking a
curriculum ladder, we look at what the user is actually slow at and drill that.
"""

import statistics
from typing import Callable


def fact_key(skill_id: str, parameters: dict) -> str | None:
    """Extract a canonical fact key from attempt parameters.

    Returns a string like:
        mul:6x7       — specific multiplication fact (commutative, sorted)
        add:2d+2d:c   — addition pattern (digit-count + carry/no-carry)
        sub:2d-1d:b   — subtraction pattern (digit-count + borrow/no-borrow)
        pct:15        — percent by percentage value

    Returns None if parameters are missing or unrecognizable.
    """
    if not parameters or not isinstance(parameters, dict):
        return None

    if skill_id == "multiplication":
        a, b = parameters.get("a"), parameters.get("b")
        if a is None or b is None:
            return None
        lo, hi = sorted([int(a), int(b)])
        return f"mul:{lo}x{hi}"

    if skill_id == "addition":
        a, b = parameters.get("a"), parameters.get("b")
        if a is None or b is None:
            return None
        carry = parameters.get("carry", False)
        da, db = _digit_bucket(a), _digit_bucket(b)
        lo, hi = sorted([da, db])
        tag = "c" if carry else "n"
        return f"add:{lo}d+{hi}d:{tag}"

    if skill_id == "subtraction":
        a, b = parameters.get("a"), parameters.get("b")
        if a is None or b is None:
            return None
        borrow = parameters.get("borrow", False)
        da, db = _digit_bucket(a), _digit_bucket(b)
        tag = "b" if borrow else "n"
        return f"sub:{da}d-{db}d:{tag}"

    if skill_id == "percent_of":
        pct = parameters.get("percentage")
        if pct is None:
            return None
        return f"pct:{int(pct)}"

    return None


def _digit_bucket(n) -> int:
    """Return digit count: 1-9 -> 1, 10-99 -> 2, 100-999 -> 3."""
    n = abs(int(n))
    if n < 10:
        return 1
    if n < 100:
        return 2
    return 3


def fact_display(key: str) -> str:
    """Human-readable label for a fact key."""
    if key.startswith("mul:"):
        parts = key[4:].split("x")
        return f"{parts[0]} x {parts[1]}"
    if key.startswith("add:"):
        rest = key[4:]  # e.g. "2d+2d:c"
        pattern, tag = rest.rsplit(":", 1)
        suffix = " (carry)" if tag == "c" else ""
        return f"{pattern}{suffix}"
    if key.startswith("sub:"):
        rest = key[4:]  # e.g. "2d-1d:b"
        pattern, tag = rest.rsplit(":", 1)
        suffix = " (borrow)" if tag == "b" else ""
        return f"{pattern}{suffix}"
    if key.startswith("pct:"):
        return f"{key[4:]}%"
    return key


def compute_fact_stats(attempts: list[dict]) -> dict[str, dict]:
    """Compute per-fact-key stats from a list of attempts.

    Returns {fact_key: {n, correct, accuracy, median_latency_ms, latencies,
                        last_seen, skill_id}}.
    """
    by_key: dict[str, list[dict]] = {}
    for a in attempts:
        if a.get("skipped"):
            continue
        params = a.get("parameters") or {}
        if isinstance(params, str):
            import json
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                continue
        sid = a.get("skill_id", "")
        key = fact_key(sid, params)
        if not key:
            continue
        by_key.setdefault(key, []).append(a)

    stats: dict[str, dict] = {}
    for key, atts in by_key.items():
        correct_count = sum(1 for a in atts if a.get("correct"))
        lats = [
            a["resolution_latency_ms"]
            for a in atts
            if a.get("resolution_latency_ms") and a.get("resolution_latency_ms") > 0
        ]
        created_times = [a.get("created_at", 0) for a in atts]
        stats[key] = {
            "n": len(atts),
            "correct": correct_count,
            "accuracy": round(correct_count / len(atts), 3) if atts else 0,
            "median_latency_ms": int(statistics.median(lats)) if lats else None,
            "latencies": sorted(lats),
            "last_seen": max(created_times) if created_times else 0,
            "skill_id": atts[0].get("skill_id", ""),
        }
    return stats


def slowest_facts(
    fact_stats: dict[str, dict],
    min_attempts: int = 2,
    limit: int = 15,
) -> list[dict]:
    """Return the slowest facts sorted by median latency descending.

    Only includes facts with at least `min_attempts` non-skipped attempts.
    """
    candidates = []
    for key, s in fact_stats.items():
        if s["n"] < min_attempts:
            continue
        if s["median_latency_ms"] is None:
            continue
        candidates.append({
            "fact_key": key,
            "display": fact_display(key),
            **s,
        })
    candidates.sort(key=lambda x: x["median_latency_ms"], reverse=True)
    return candidates[:limit]


def worst_accuracy_facts(
    fact_stats: dict[str, dict],
    min_attempts: int = 3,
    limit: int = 10,
) -> list[dict]:
    """Return facts with the worst accuracy (most errors)."""
    candidates = []
    for key, s in fact_stats.items():
        if s["n"] < min_attempts:
            continue
        if s["accuracy"] >= 1.0:
            continue
        candidates.append({
            "fact_key": key,
            "display": fact_display(key),
            **s,
        })
    candidates.sort(key=lambda x: x["accuracy"])
    return candidates[:limit]


def recent_regressions(
    attempts: list[dict],
    window_old: int = 20,
    window_recent: int = 5,
    min_old: int = 3,
    min_recent: int = 2,
) -> list[dict]:
    """Find facts where recent latency is notably worse than older latency.

    Compares the most recent `window_recent` attempts per fact against
    the `window_old` oldest attempts. A regression is flagged when the
    recent median is >30% slower than the old median.
    """
    # Group attempts by fact key, ordered by time
    by_key: dict[str, list[dict]] = {}
    for a in attempts:
        if a.get("skipped"):
            continue
        params = a.get("parameters") or {}
        if isinstance(params, str):
            import json
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                continue
        sid = a.get("skill_id", "")
        key = fact_key(sid, params)
        if not key:
            continue
        by_key.setdefault(key, []).append(a)

    regressions = []
    for key, atts in by_key.items():
        # Sort by created_at
        atts.sort(key=lambda a: a.get("created_at", 0))
        lats = [
            (a.get("resolution_latency_ms"), a.get("created_at", 0))
            for a in atts
            if a.get("resolution_latency_ms") and a.get("resolution_latency_ms") > 0
        ]
        if len(lats) < min_old + min_recent:
            continue

        old_lats = [l for l, _ in lats[:window_old]]
        recent_lats = [l for l, _ in lats[-window_recent:]]

        if len(old_lats) < min_old or len(recent_lats) < min_recent:
            continue

        old_med = statistics.median(old_lats)
        recent_med = statistics.median(recent_lats)

        if old_med <= 0:
            continue

        ratio = recent_med / old_med
        if ratio > 1.3:  # 30% slower
            regressions.append({
                "fact_key": key,
                "display": fact_display(key),
                "old_median_ms": int(old_med),
                "recent_median_ms": int(recent_med),
                "regression_ratio": round(ratio, 2),
                "skill_id": atts[0].get("skill_id", ""),
            })

    regressions.sort(key=lambda x: x["regression_ratio"], reverse=True)
    return regressions


def drill_priorities(
    fact_stats: dict[str, dict],
    skill_target_latencies: dict[str, int],
    min_attempts: int = 2,
    limit: int = 10,
) -> list[dict]:
    """Rank facts by how much speed gain is available.

    Priority = (current_median - target) / target, clipped at 0.
    Higher means more room for improvement. This answers "what should the
    scheduler drill next?"
    """
    candidates = []
    for key, s in fact_stats.items():
        if s["n"] < min_attempts:
            continue
        if s["median_latency_ms"] is None:
            continue
        target = skill_target_latencies.get(s["skill_id"])
        if not target:
            continue
        gap = (s["median_latency_ms"] - target) / target
        if gap <= 0:
            continue
        # Also penalize low accuracy heavily
        acc_penalty = 1.0 + (1.0 - s["accuracy"]) * 2.0
        priority = gap * acc_penalty
        candidates.append({
            "fact_key": key,
            "display": fact_display(key),
            "priority": round(priority, 3),
            "gap_ratio": round(gap, 3),
            "median_latency_ms": s["median_latency_ms"],
            "target_ms": target,
            "accuracy": s["accuracy"],
            "n": s["n"],
            "skill_id": s["skill_id"],
        })
    candidates.sort(key=lambda x: x["priority"], reverse=True)
    return candidates[:limit]
