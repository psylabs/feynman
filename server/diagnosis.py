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


_DIGIT_WORDS = {1: "single-digit", 2: "two-digit", 3: "three-digit"}


def _digit_phrase(token: str) -> str:
    """'2d' -> 'two-digit'."""
    if token.endswith("d") and token[:-1].isdigit():
        return _DIGIT_WORDS.get(int(token[:-1]), token)
    return token


def fact_display(key: str) -> str:
    """Plain-English label for a fact key.

    Examples:
        mul:6x7      -> "6 x 7"
        add:2d+2d:c  -> "Two-digit + two-digit, with carry"
        sub:2d-1d:n  -> "Two-digit − single-digit"
        pct:15       -> "15%"
    """
    if key.startswith("mul:"):
        parts = key[4:].split("x")
        return f"{parts[0]} x {parts[1]}"
    if key.startswith("add:"):
        rest = key[4:]
        pattern, tag = rest.rsplit(":", 1)
        left, right = pattern.split("+", 1)
        base = f"{_digit_phrase(left).capitalize()} + {_digit_phrase(right)}"
        return f"{base}, with carry" if tag == "c" else base
    if key.startswith("sub:"):
        rest = key[4:]
        pattern, tag = rest.rsplit(":", 1)
        left, right = pattern.split("-", 1)
        base = f"{_digit_phrase(left).capitalize()} − {_digit_phrase(right)}"
        return f"{base}, with borrow" if tag == "b" else base
    if key.startswith("pct:"):
        return f"{key[4:]}%"
    return key


def confidence_band(n: int) -> str:
    """Map attempt count to a claim-strength label.

    <5  -> "low"   (a single fast trial moves the median noticeably)
    5-14 -> "medium"
    >=15 -> "high"
    """
    if n is None:
        return "low"
    if n < 5:
        return "low"
    if n < 15:
        return "medium"
    return "high"


def factor_family_stats(fact_stats: dict[str, dict]) -> list[dict]:
    """Roll up multiplication facts by factor family (×7 facts, ×8 facts, …).

    A fact like mul:6x7 contributes to BOTH the ×6 and ×7 families. Single-
    factor facts (like 7×7) contribute once. Returns a list sorted by median
    latency descending so families that need the most work surface first.
    """
    by_factor: dict[int, dict] = {}
    for key, s in fact_stats.items():
        if not key.startswith("mul:"):
            continue
        try:
            a_str, b_str = key[4:].split("x")
            a, b = int(a_str), int(b_str)
        except (ValueError, IndexError):
            continue
        for f in {a, b}:
            row = by_factor.setdefault(f, {"factor": f, "n": 0, "correct": 0, "lats": []})
            row["n"] += s["n"]
            row["correct"] += s["correct"]
            row["lats"].extend(s.get("latencies") or [])

    out = []
    for f, row in by_factor.items():
        lats = row["lats"]
        med = int(statistics.median(lats)) if lats else None
        out.append({
            "factor": f,
            "display": f"×{f} facts",
            "n": row["n"],
            "accuracy": round(row["correct"] / row["n"], 3) if row["n"] else 0,
            "median_latency_ms": med,
            "confidence": confidence_band(row["n"]),
        })
    out.sort(key=lambda r: r["median_latency_ms"] or 0, reverse=True)
    return out


def diagnosis_summary(
    fact_stats: dict[str, dict],
    skill_target_latencies: dict[str, int],
    attempts: list[dict],
) -> dict:
    """Compact teaser shape for the start screen and the review screen.

    Returns:
        total_attempts: int
        confidence: "low" | "medium" | "high" — overall claim strength
        focus: list of up to 3 facts the next drill is most likely to target
        regression: optional one regression highlight (or None)
        notable_mastered: optional one fast-and-accurate fact (or None)
    """
    regs = recent_regressions(attempts)
    reg_keys = {r["fact_key"] for r in regs}
    priorities = drill_priorities(
        fact_stats, skill_target_latencies, min_attempts=2, limit=3,
        regression_keys=reg_keys,
    )
    total = sum(s["n"] for s in fact_stats.values())

    # Overall confidence: how many facts have we seen enough of?
    high_n_facts = sum(1 for s in fact_stats.values() if s["n"] >= 15)
    if total < 20 or not priorities:
        overall = "low"
    elif high_n_facts < 3:
        overall = "medium"
    else:
        overall = "high"

    focus = []
    for p in priorities:
        focus.append({
            "fact_key": p["fact_key"],
            "display": p["display"],
            "skill_id": p["skill_id"],
            "median_latency_ms": p["median_latency_ms"],
            "target_ms": p["target_ms"],
            "accuracy": p["accuracy"],
            "n": p["n"],
            "confidence": confidence_band(p["n"]),
        })

    regression = None
    if regs:
        r = regs[0]
        regression = {
            "fact_key": r["fact_key"],
            "display": r["display"],
            "old_median_ms": r["old_median_ms"],
            "recent_median_ms": r["recent_median_ms"],
            "regression_ratio": r["regression_ratio"],
        }

    # Notable mastered: pick a fact that's accurate and at-or-below target
    notable = None
    for key, s in fact_stats.items():
        target = skill_target_latencies.get(s["skill_id"])
        if not target or s["median_latency_ms"] is None:
            continue
        if s["accuracy"] >= 0.9 and s["median_latency_ms"] <= target and s["n"] >= 5:
            notable = {
                "fact_key": key,
                "display": fact_display(key),
                "median_latency_ms": s["median_latency_ms"],
                "target_ms": target,
                "n": s["n"],
            }
            break

    return {
        "total_attempts": total,
        "confidence": overall,
        "focus": focus,
        "regression": regression,
        "notable_mastered": notable,
    }


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
            "confidence": confidence_band(s["n"]),
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
            "confidence": confidence_band(s["n"]),
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
    regression_keys: set[str] | None = None,
) -> list[dict]:
    """Rank facts by how much speed gain is available.

    Priority = (current_median - target) / target, clipped at 0.
    Higher means more room for improvement. This answers "what should the
    scheduler drill next?"

    Facts in `regression_keys` get a 1.5× boost: a fact that was fast and is
    now slow is a stronger signal than a fact that was always slow.
    """
    regression_keys = regression_keys or set()
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
        regressed = key in regression_keys
        if regressed:
            priority *= 1.5
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
            "regressed": regressed,
        })
    candidates.sort(key=lambda x: x["priority"], reverse=True)
    return candidates[:limit]


def mastered_for_retention(
    fact_stats: dict[str, dict],
    skill_target_latencies: dict[str, int],
    now: float | None = None,
    n_min: int = 5,
    accuracy_min: float = 0.9,
    target_slack: float = 1.1,
    limit: int = 10,
) -> list[dict]:
    """Mastered facts ranked by how long since the user last saw them.

    A fact qualifies as mastered when accuracy ≥ `accuracy_min`, n ≥ `n_min`,
    and median latency ≤ target × `target_slack`. Result is sorted by recency
    descending so the most-overdue retention check sorts first.
    """
    import time as _time
    now = now if now is not None else _time.time()
    out = []
    for key, s in fact_stats.items():
        target = skill_target_latencies.get(s["skill_id"])
        if not target or s["median_latency_ms"] is None:
            continue
        if s["n"] < n_min or s["accuracy"] < accuracy_min:
            continue
        if s["median_latency_ms"] > target * target_slack:
            continue
        last_seen = s.get("last_seen") or 0
        days_since = (now - last_seen) / 86400 if last_seen else 999.0
        out.append({
            "fact_key": key,
            "display": fact_display(key),
            "skill_id": s["skill_id"],
            "median_latency_ms": s["median_latency_ms"],
            "target_ms": target,
            "accuracy": s["accuracy"],
            "n": s["n"],
            "days_since_seen": round(days_since, 1),
        })
    out.sort(key=lambda x: x["days_since_seen"], reverse=True)
    return out[:limit]
