"""Summaries that make a drill session's intent and outcome explicit."""

import statistics
from collections import Counter, defaultdict

from server import diagnosis


ROLE_LABELS = {
    "theme": "focused",
    "related": "related",
    "retention": "retention",
    "grounded": "real-life",
    "exploration": "exploration",
}


def plan_summary(slots: list[dict]) -> dict:
    """Summarize a planned drill in user-facing terms."""
    counts = _role_counts(slots)
    focus_counts = Counter(
        _display_for_slot(s)
        for s in slots
        if s.get("role") == "theme"
    )
    focus = [name for name, _ in focus_counts.most_common(3)]
    grounded_skills = sorted({
        s.get("skill_id") for s in slots if s.get("role") == "grounded"
    } - {None})
    return {
        "focus": focus,
        "counts": counts,
        "grounded_skills": grounded_skills,
        "intent": _intent_sentence(focus, counts.get("grounded", 0), grounded_skills),
        "mix": _mix_sentence(counts),
    }


def review_analysis(slots: list[dict], attempts: list[dict], exploratory: bool = False) -> dict:
    """Compare completed attempts against the session plan."""
    by_position = {
        int(a.get("position_in_session")): a
        for a in attempts
        if a.get("position_in_session") is not None
    }
    role_rows: dict[str, list[dict]] = defaultdict(list)
    focus_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    focus_meta: dict[tuple[str, str], dict] = {}
    correct_attempts = []

    for idx, slot in enumerate(slots, start=1):
        attempt = by_position.get(idx)
        if not attempt:
            continue
        role = slot.get("role") or "theme"
        role_rows[role].append(attempt)
        if attempt.get("correct") and attempt.get("resolution_latency_ms") is not None:
            correct_attempts.append(_attempt_row(slot, attempt))
        if role == "theme":
            key = (slot.get("fact_key") or _display_for_slot(slot), _display_for_slot(slot))
            focus_rows[key].append(attempt)
            focus_meta[key] = slot

    role_stats = {
        role: _attempt_stats(rows)
        for role, rows in role_rows.items()
    }
    focus_stats = [
        _focus_stat(focus_meta[key], rows)
        for key, rows in focus_rows.items()
    ]
    focus_stats.sort(key=lambda r: (r["accuracy"], -(r["median_correct_latency_ms"] or r["median_latency_ms"] or 0)))
    fluency_gaps = [r for r in focus_stats if r.get("fluency_status") == "slow"]
    fluency_gaps.sort(key=lambda r: r.get("gap_ratio") or 0, reverse=True)
    slowest_correct = sorted(correct_attempts, key=lambda r: r["latency_ms"], reverse=True)[:3]

    plan = plan_summary(slots)
    if exploratory:
        plan["intent"] = "This session was exploratory; the analysis below is based on the facts you just answered."
        plan["mix"] = "Mix: exploratory."

    return {
        "plan": plan,
        "role_stats": role_stats,
        "focus_stats": focus_stats,
        "fluency_gaps": fluency_gaps,
        "slowest_correct": slowest_correct,
        "moved": _moved_lines(focus_stats),
        "still_weak": _weak_lines(focus_stats),
        "next_time": _next_time_line(focus_stats),
    }


def exploratory_review_analysis(
    attempts: list[dict],
    fact_stats: dict[str, dict],
    skill_target_latencies: dict[str, int],
) -> dict | None:
    """Build session analysis for drills that had no precomputed plan."""
    slots = []
    for attempt in attempts:
        fact_key = _fact_key_for_attempt(attempt)
        if not fact_key:
            continue
        stat = fact_stats.get(fact_key) or {}
        skill_id = attempt.get("skill_id")
        target_ms = skill_target_latencies.get(skill_id)
        slots.append({
            "role": "theme",
            "skill_id": skill_id,
            "fact_key": fact_key,
            "display": diagnosis.fact_display(fact_key),
            "target_ms": target_ms,
            "diagnosis_median_latency_ms": stat.get("median_latency_ms"),
            "diagnosis_accuracy": stat.get("accuracy"),
            "diagnosis_n": stat.get("n"),
        })
    if not slots:
        return None
    return review_analysis(slots, attempts, exploratory=True)


def _role_counts(slots: list[dict]) -> dict[str, int]:
    counts = {role: 0 for role in ROLE_LABELS}
    for slot in slots:
        role = slot.get("role")
        if role in counts:
            counts[role] += 1
    return counts


def _display_for_slot(slot: dict) -> str:
    return slot.get("display") or slot.get("fact_key") or "this focus"


_GROUNDED_LABEL = {
    "money_arithmetic": "spending",
    "weather_math": "weather",
}


def _intent_sentence(focus: list[str], grounded_count: int = 0, grounded_skills: list[str] | None = None) -> str:
    grounded_skills = grounded_skills or []
    grounded_phrase = ""
    if grounded_count:
        labels = [_GROUNDED_LABEL.get(s, s.replace("_", " ")) for s in grounded_skills]
        if len(labels) == 1:
            source = labels[0]
        elif len(labels) == 2:
            source = f"{labels[0]} and {labels[1]}"
        else:
            source = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        grounded_phrase = f" Plus {grounded_count} real-life drills from your {source}." if focus \
            else f"This session is {grounded_count} real-life drills from your {source}."

    if not focus:
        if grounded_phrase:
            return grounded_phrase.strip()
        return "This session is exploratory while the system gathers enough data."
    if len(focus) == 1:
        return f"This session is mainly drilling {focus[0]}.{grounded_phrase}"
    return f"This session is mainly drilling {', '.join(focus[:-1])}, and {focus[-1]}.{grounded_phrase}"


def _mix_sentence(counts: dict[str, int]) -> str:
    parts = []
    for role in ("theme", "related", "grounded", "retention", "exploration"):
        n = counts.get(role, 0)
        if n:
            parts.append(f"{n} {ROLE_LABELS[role]}")
    return "Mix: " + ", ".join(parts) + "." if parts else "Mix: exploratory."


def _attempt_stats(rows: list[dict]) -> dict:
    total = len(rows)
    correct = sum(1 for r in rows if r.get("correct"))
    latencies = [
        int(r["resolution_latency_ms"])
        for r in rows
        if r.get("resolution_latency_ms") is not None
    ]
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else None,
        "median_latency_ms": int(statistics.median(latencies)) if latencies else None,
    }


def _fact_key_for_attempt(attempt: dict) -> str | None:
    params = attempt.get("parameters") or {}
    if isinstance(params, str):
        import json
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            return None
    return diagnosis.fact_key(attempt.get("skill_id", ""), params)


def _focus_stat(slot: dict, rows: list[dict]) -> dict:
    stats = _attempt_stats(rows)
    correct_rows = [r for r in rows if r.get("correct")]
    correct_latencies = [
        int(r["resolution_latency_ms"])
        for r in correct_rows
        if r.get("resolution_latency_ms") is not None
    ]
    correct_onsets = [
        int(r["onset_latency_ms"])
        for r in correct_rows
        if r.get("onset_latency_ms") is not None
    ]
    median_correct = int(statistics.median(correct_latencies)) if correct_latencies else None
    median_onset = int(statistics.median(correct_onsets)) if correct_onsets else None
    target = slot.get("target_ms")
    gap_ms = median_correct - target if target and median_correct is not None else None
    gap_ratio = round(gap_ms / target, 3) if gap_ms is not None and target else None
    status = "unknown"
    if target and median_correct is not None:
        status = "slow" if median_correct > target else "fluent"
    return {
        "skill_id": slot.get("skill_id"),
        "fact_key": slot.get("fact_key"),
        "display": _display_for_slot(slot),
        "target_ms": target,
        "baseline_median_latency_ms": slot.get("diagnosis_median_latency_ms"),
        "baseline_accuracy": slot.get("diagnosis_accuracy"),
        "baseline_n": slot.get("diagnosis_n"),
        "median_correct_latency_ms": median_correct,
        "median_correct_onset_ms": median_onset,
        "gap_ms": gap_ms,
        "gap_ratio": gap_ratio,
        "fluency_status": status,
        "interpretation": _latency_interpretation(median_onset, median_correct),
        **stats,
    }


def _attempt_row(slot: dict, attempt: dict) -> dict:
    latency = int(attempt["resolution_latency_ms"])
    target = slot.get("target_ms")
    return {
        "skill_id": slot.get("skill_id"),
        "fact_key": slot.get("fact_key"),
        "display": _display_for_slot(slot),
        "role": slot.get("role") or "theme",
        "latency_ms": latency,
        "target_ms": target,
        "gap_ms": latency - target if target else None,
        "position": attempt.get("position_in_session"),
    }


def _latency_interpretation(onset_ms: int | None, resolution_ms: int | None) -> str | None:
    if onset_ms is None or resolution_ms is None or resolution_ms <= 0:
        return None
    if onset_ms / resolution_ms >= 0.45:
        return "retrieval hesitation"
    return "calculation effort"


def _moved_lines(focus_stats: list[dict]) -> list[str]:
    lines = []
    for row in focus_stats:
        if row["total"] and row["accuracy"] == 1 and row.get("fluency_status") == "fluent":
            lines.append(
                f"{row['display']} met the fluency target ({row['correct']}/{row['total']} correct, median {row['median_correct_latency_ms']}ms vs target {row['target_ms']}ms)."
            )
    return lines[:2]


def _weak_lines(focus_stats: list[dict]) -> list[str]:
    lines = []
    for row in focus_stats:
        if not row["total"]:
            continue
        if row["accuracy"] is not None and row["accuracy"] < 1:
            lines.append(
                f"{row['display']} still needs work ({row['correct']}/{row['total']} correct)."
            )
        elif row.get("fluency_status") == "slow":
            lines.append(
                f"{row['display']} was correct but still slow — median {row['median_correct_latency_ms']}ms vs target {row['target_ms']}ms."
            )
    return lines[:2]


def _next_time_line(focus_stats: list[dict]) -> str:
    weak = _weak_lines(focus_stats)
    if weak and focus_stats:
        return f"Next time should keep {focus_stats[0]['display']} in the focused mix."
    if focus_stats:
        return "Next time can keep these in rotation and probe the next slowest facts."
    return "Next time should remain exploratory until there is enough data to focus."
