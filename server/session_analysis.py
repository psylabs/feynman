"""Summaries that make a drill session's intent and outcome explicit."""

import json
import statistics
from collections import Counter, defaultdict

from server import diagnosis, strategies, taxonomy


# Singular/plural forms for new rewritten-scheduler roles.
_ROLE_SINGULAR = {
    "due": "review due",
    "bootstrap": "first look",
    "weak": "weak-spot drill",
}

_ROLE_PLURAL = {
    "due": "reviews due",
    "bootstrap": "first looks",
    "weak": "weak-spot drills",
}

# Legacy roles use count-invariant adjective labels; new roles get plural forms
# (singular selected at render time in _mix_sentence).
ROLE_LABELS = {
    "theme": "focused",
    "related": "related",
    "retention": "retention",
    "grounded": "real-life",
    "exploration": "exploration",
    **_ROLE_PLURAL,
}

# Roles the pre-rewrite scheduler could emit; always initialized in counts so
# legacy plans (and dict-equality tests against them) keep their exact shape.
_LEGACY_ROLES = ("theme", "related", "retention", "grounded", "exploration")

# Display order for the mix sentence: rewritten-scheduler roles first, then
# the legacy ones.
_MIX_ROLE_ORDER = ("due", "bootstrap", "weak", "theme", "related", "grounded", "retention", "exploration")


def plan_summary(slots: list[dict]) -> dict:
    """Summarize a planned drill in user-facing terms."""
    counts = _role_counts(slots)

    theme_slots = [s for s in slots if s.get("role") == "theme"]
    if theme_slots:
        focus_counts = Counter(_display_for_slot(s) for s in theme_slots)
    else:
        # Rewritten scheduler: no theme slots, so derive focus from what the
        # session is actually clearing/drilling (due reviews + weak spots).
        dw_slots = [
            s for s in slots
            if _base_role(s) in ("due", "weak") and s.get("family")
        ]
        focus_counts = Counter(taxonomy.family_display(s["family"]) for s in dw_slots)
    focus = [name for name, _ in focus_counts.most_common(3)]

    grounded_skills = sorted({
        s.get("skill_id") for s in slots
        if s.get("role") == "grounded" or (s.get("role") or "").endswith("+skin")
    } - {None})

    bootstrap_families = list(dict.fromkeys(
        taxonomy.family_display(s["family"])
        for s in slots
        if _base_role(s) == "bootstrap" and s.get("family")
    ))

    return {
        "focus": focus,
        "counts": counts,
        "grounded_skills": grounded_skills,
        "intent": _intent_sentence(
            focus, counts, grounded_skills, bootstrap_families,
            has_theme=bool(theme_slots),
        ),
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
        _attach_tip(slot, attempt)
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
        "stratification": _session_stratification(attempts),
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


def pattern_analysis(attempts: list[dict]) -> list[dict]:
    correct = [
        a for a in attempts
        if a.get("correct") and a.get("resolution_latency_ms") is not None and a.get("skill_id")
    ]
    if not correct:
        return []
    by_skill: dict[str, list[int]] = defaultdict(list)
    for a in correct:
        by_skill[a["skill_id"]].append(int(a["resolution_latency_ms"]))
    baseline = {sid: statistics.median(lats) for sid, lats in by_skill.items()}
    groups: dict[tuple, list[int]] = defaultdict(list)
    for a in correct:
        params = a.get("parameters") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (TypeError, ValueError):
                continue
        for key, val in (params.get("features") or {}).items():
            groups[(a["skill_id"], key, val)].append(int(a["resolution_latency_ms"]))
    results = []
    for (skill_id, fkey, fval), lats in groups.items():
        if len(lats) < 5:
            continue
        b = baseline.get(skill_id, 0)
        if not b:
            continue
        ratio = statistics.median(lats) / b
        if ratio <= 1.4:
            continue
        results.append({
            "skill_id": skill_id,
            "feature_key": fkey,
            "feature_value": fval,
            "ratio": round(ratio, 2),
            "n": len(lats),
            "group_median_ms": int(statistics.median(lats)),
            "baseline_ms": int(b),
        })
    return sorted(results, key=lambda r: r["ratio"], reverse=True)[:3]


_STRATIFY_CONFIG: dict[str, tuple[str, dict | None]] = {
    "subtraction": ("borrow", {True: "with borrow", False: "no borrow"}),
    "addition": ("carry", {True: "with carry", False: "no carry"}),
    "weather_math": ("operation", None),
}


def _session_stratification(attempts: list[dict]) -> list[dict]:
    by_skill: dict[str, list[dict]] = defaultdict(list)
    for a in attempts:
        by_skill[a.get("skill_id", "")].append(a)
    result = []
    for skill_id, rows in by_skill.items():
        config = _STRATIFY_CONFIG.get(skill_id)
        if not config:
            continue
        key, labels = config
        buckets: dict = defaultdict(list)
        for a in rows:
            params = a.get("parameters") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except (TypeError, ValueError):
                    continue
            val = params.get(key)
            if val is not None:
                buckets[val].append(a)
        valid = {k: v for k, v in buckets.items() if len(v) >= 2}
        if len(valid) < 2:
            continue
        strat_rows = []
        for val, bucket in sorted(valid.items(), key=lambda x: str(x[0])):
            label = (labels or {}).get(val, str(val)) if labels else str(val)
            strat_rows.append({"label": label, "param_value": val, **_attempt_stats(bucket)})
        result.append({"skill_id": skill_id, "param_key": key, "rows": strat_rows})
    return result


def _base_role(slot: dict) -> str:
    """A slot's role with any '+skin' suffix stripped (e.g. 'weak+skin' -> 'weak')."""
    return (slot.get("role") or "").split("+", 1)[0]


def _role_counts(slots: list[dict]) -> dict[str, int]:
    counts = {role: 0 for role in _LEGACY_ROLES}
    for slot in slots:
        role = slot.get("role") or ""
        base = role.split("+", 1)[0]
        if base in ROLE_LABELS:
            counts[base] = counts.get(base, 0) + 1
        # Skinned slots render as money/weather problems, so they also count
        # toward the "grounded" display bucket (in addition to their base role).
        if role.endswith("+skin") and base != "grounded":
            counts["grounded"] = counts.get("grounded", 0) + 1
    return counts


def _display_for_slot(slot: dict) -> str:
    return slot.get("display") or slot.get("fact_key") or "this focus"


_GROUNDED_LABEL = {
    "money_arithmetic": "spending",
    "weather_math": "weather",
}


def _join_and(items: list[str]) -> str:
    """Join items as 'a', 'a and b', or 'a, b, and c'."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _intent_sentence(
    focus: list[str],
    counts: dict[str, int],
    grounded_skills: list[str] | None = None,
    bootstrap_families: list[str] | None = None,
    has_theme: bool = False,
) -> str:
    grounded_skills = grounded_skills or []
    bootstrap_families = bootstrap_families or []
    grounded_count = counts.get("grounded", 0)
    due_count = counts.get("due", 0)
    weak_count = counts.get("weak", 0)

    lead = ""
    if has_theme and focus:
        if len(focus) == 1:
            lead = f"This session is mainly drilling {focus[0]}."
        else:
            lead = f"This session is mainly drilling {', '.join(focus[:-1])}, and {focus[-1]}."
    elif not has_theme and (due_count or weak_count):
        head = "mainly clearing overdue reviews" if due_count >= weak_count else "mainly drilling weak spots"
        body = f"This session is {head}"
        if focus:
            body += f": {', '.join(focus)}"
        if bootstrap_families:
            body += f", plus a first look at {_join_and(bootstrap_families)}"
        lead = body + "."
    elif not has_theme and bootstrap_families:
        lead = f"This session is mainly a first look at {_join_and(bootstrap_families)}."

    grounded_phrase = ""
    if grounded_count:
        labels = [_GROUNDED_LABEL.get(s, s.replace("_", " ")) for s in grounded_skills]
        source = _join_and(labels)
        grounded_phrase = f" Plus {grounded_count} real-life drills from your {source}." if lead \
            else f"This session is {grounded_count} real-life drills from your {source}."

    if not lead:
        if grounded_phrase:
            return grounded_phrase.strip()
        return "This session is exploratory while the system gathers enough data."
    return f"{lead}{grounded_phrase}"


def _mix_sentence(counts: dict[str, int]) -> str:
    parts = []
    for role in _MIX_ROLE_ORDER:
        n = counts.get(role, 0)
        if n:
            # Select singular or plural form for new roles; legacy roles use
            # count-invariant adjectives from ROLE_LABELS.
            if n == 1 and role in _ROLE_SINGULAR:
                label = _ROLE_SINGULAR[role]
            else:
                label = ROLE_LABELS[role]
            parts.append(f"{n} {label}")
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
        if not row["total"] or row["total"] < 5:
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


def _attach_tip(slot: dict, attempt: dict) -> None:
    """Attach a strategy tip to an attempt dict if it is wrong or slow.

    Mutates the attempt in place: sets ``attempt["tip"]`` to the first verified
    tip, or leaves the key absent.  Skipped attempts are always left alone.
    """
    if attempt.get("skipped"):
        return

    skill_id = slot.get("skill_id") or attempt.get("skill_id")
    params = attempt.get("parameters") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            params = {}

    expected_raw = attempt.get("expected_answer")
    try:
        expected = float(expected_raw) if expected_raw is not None else None
    except (TypeError, ValueError):
        expected = None

    if not skill_id or expected is None:
        return

    correct = attempt.get("correct")
    wrong = not correct
    slow = False

    if correct:
        taxon = taxonomy.classify(skill_id, params)
        if taxon is not None:
            if taxon.tier == "primitive":
                latency = attempt.get("onset_latency_ms")
                tier_target: float = taxonomy.PRIMITIVE_ONSET_TARGET_MS
            else:
                latency = attempt.get("resolution_latency_ms")
                tier_target = slot.get("target_ms")  # type: ignore[assignment]
            if latency is not None and tier_target is not None:
                slow = latency > tier_target * 1.25

    if wrong or slow:
        tips = strategies.tips_for(skill_id, params, expected,
                                   tolerance=slot.get("tolerance"))
        if tips:
            attempt["tip"] = tips[0]
