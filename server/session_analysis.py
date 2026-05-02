"""Summaries that make a drill session's intent and outcome explicit."""

import statistics
from collections import Counter, defaultdict


ROLE_LABELS = {
    "theme": "focused",
    "related": "related",
    "retention": "retention",
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
    return {
        "focus": focus,
        "counts": counts,
        "intent": _intent_sentence(focus),
        "mix": _mix_sentence(counts),
    }


def review_analysis(slots: list[dict], attempts: list[dict]) -> dict:
    """Compare completed attempts against the session plan."""
    by_position = {
        int(a.get("position_in_session")): a
        for a in attempts
        if a.get("position_in_session") is not None
    }
    role_rows: dict[str, list[dict]] = defaultdict(list)
    focus_rows: dict[str, list[dict]] = defaultdict(list)

    for idx, slot in enumerate(slots, start=1):
        attempt = by_position.get(idx)
        if not attempt:
            continue
        role = slot.get("role") or "theme"
        role_rows[role].append(attempt)
        if role == "theme":
            focus_rows[_display_for_slot(slot)].append(attempt)

    role_stats = {
        role: _attempt_stats(rows)
        for role, rows in role_rows.items()
    }
    focus_stats = [
        {"display": display, **_attempt_stats(rows)}
        for display, rows in focus_rows.items()
    ]
    focus_stats.sort(key=lambda r: (r["accuracy"], -(r["median_latency_ms"] or 0)))

    return {
        "plan": plan_summary(slots),
        "role_stats": role_stats,
        "focus_stats": focus_stats,
        "moved": _moved_lines(focus_stats),
        "still_weak": _weak_lines(focus_stats),
        "next_time": _next_time_line(focus_stats),
    }


def _role_counts(slots: list[dict]) -> dict[str, int]:
    counts = {"theme": 0, "related": 0, "retention": 0}
    for slot in slots:
        role = slot.get("role")
        if role in counts:
            counts[role] += 1
    return counts


def _display_for_slot(slot: dict) -> str:
    return slot.get("display") or slot.get("fact_key") or "this focus"


def _intent_sentence(focus: list[str]) -> str:
    if not focus:
        return "This session is exploratory while the system gathers enough data."
    if len(focus) == 1:
        return f"This session is mainly drilling {focus[0]}."
    return f"This session is mainly drilling {', '.join(focus[:-1])}, and {focus[-1]}."


def _mix_sentence(counts: dict[str, int]) -> str:
    parts = []
    for role in ("theme", "related", "retention"):
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


def _moved_lines(focus_stats: list[dict]) -> list[str]:
    lines = []
    for row in focus_stats:
        if row["total"] and row["accuracy"] == 1:
            lines.append(
                f"{row['display']} was accurate in this session ({row['correct']}/{row['total']})."
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
    return lines[:2]


def _next_time_line(focus_stats: list[dict]) -> str:
    weak = _weak_lines(focus_stats)
    if weak and focus_stats:
        return f"Next time should keep {focus_stats[0]['display']} in the focused mix."
    if focus_stats:
        return "Next time can keep these in rotation and probe the next slowest facts."
    return "Next time should remain exploratory until there is enough data to focus."
