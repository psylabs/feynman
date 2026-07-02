"""Per-family aggregates for the taxonomy-based scheduler.

Three public functions:
  family_stats   — raw aggregates per family
  weak_families  — ranked list of families needing drilling
  family_level   — evidence-based level (1–3) for one family

Attempt rows are dicts as returned by storage.all_attempts_for_user:
parameters is already a dict (or a JSON string as a fallback). Fields consumed:
  skill_id, parameters, correct, skipped, onset_latency_ms,
  resolution_latency_ms, created_at.

Design notes / micro-ambiguity decisions:
  - family_stats: "excluded" means skipped=True; the function still accepts any
    attempt list (callers using all_attempts_for_user get skipped=0 rows already,
    but we double-check for callers that pass mixed lists).
  - weak_families: families whose skill is absent from skill_targets get
    target=None.  Without a target we cannot confirm "within target", so such
    families are NOT excluded on the mastery check (they remain candidates).
    Their latency ratio in the sort key is 0.0 (sorted purely by acc).
  - family_level advance: checked sequentially — if level 1 does not pass we
    break immediately and do not check level 2.  "Last 10+" means ≥10 attempts
    at that level; we then check the LAST 10 of them.  Level comes from
    parameters["level"].  For compound families where no target_ms is provided,
    the latency check is skipped (treated as "always within target") so
    advancement depends only on accuracy.
"""

import json
import statistics
from typing import Optional

from server.taxonomy import PRIMITIVE_ONSET_TARGET_MS, classify

# ---------------------------------------------------------------------------
# Family-prefix → skill_id mapping (used by weak_families to look up targets)
# ---------------------------------------------------------------------------
_PREFIX_TO_SKILL: dict[str, str] = {
    "add": "addition",
    "sub": "subtraction",
    "mul": "multiplication",
    "div": "division",
    "money": "money_arithmetic",
    "weather": "weather_math",
    "pct": "percent_of",
}

def _parse_params(a: dict) -> dict:
    """Return parameters as a dict, parsing JSON strings if needed.

    Mirrors the pattern in diagnosis.compute_fact_stats so both modules handle
    the same raw row shapes (sqlite3.Row converted to dict, or plain dicts from
    test fixtures).
    """
    params = a.get("parameters") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (TypeError, ValueError):
            return {}
    return params


def _latency(a: dict, tier: str) -> Optional[int]:
    """Return the appropriate latency for one attempt, or None if unavailable."""
    field = "onset_latency_ms" if tier == "primitive" else "resolution_latency_ms"
    v = a.get(field)
    return int(v) if (v is not None and v > 0) else None


def _skill_for_family(family: str) -> str:
    """Derive the originating skill_id from a family string."""
    prefix = family.split(".")[0]
    return _PREFIX_TO_SKILL.get(prefix, prefix)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def family_stats(attempts: list[dict]) -> dict[str, dict]:
    """Compute per-family aggregates from a list of attempt rows.

    Returns:
        {family: {"tier", "n", "acc", "median_ms", "last_seen_at"}}

    - n:           non-skipped attempts classified to this family
    - acc:         fraction correct (rounded to 3 dp)
    - median_ms:   median latency over CORRECT attempts only (onset for primitive,
                   resolution for compound); None when no correct attempts have data
    - last_seen_at: max created_at across all family attempts; None if unavailable
    """
    by_family: dict[str, list[dict]] = {}
    tier_map: dict[str, str] = {}

    for a in attempts:
        if a.get("skipped"):
            continue
        params = _parse_params(a)
        taxon = classify(a.get("skill_id", ""), params)
        if taxon is None:
            continue
        fam = taxon.family
        by_family.setdefault(fam, []).append(a)
        tier_map[fam] = taxon.tier

    result: dict[str, dict] = {}
    for fam, atts in by_family.items():
        tier = tier_map[fam]
        n = len(atts)
        correct_count = sum(1 for a in atts if a.get("correct"))
        acc = round(correct_count / n, 3) if n else 0.0

        correct_lats = [
            lat
            for a in atts
            if a.get("correct")
            for lat in [_latency(a, tier)]
            if lat is not None
        ]
        median_ms = int(statistics.median(correct_lats)) if correct_lats else None

        times = [a.get("created_at") for a in atts if a.get("created_at") is not None]
        last_seen_at = max(times) if times else None

        result[fam] = {
            "tier": tier,
            "n": n,
            "acc": acc,
            "median_ms": median_ms,
            "last_seen_at": last_seen_at,
        }

    return result


def weak_families(
    stats: dict[str, dict],
    skill_targets: dict[str, int],
    limit: int = 10,
) -> list[str]:
    """Return a ranked list of families that need drilling.

    Filters:
      - n >= 5 (insufficient data otherwise)
      - excludes families where acc >= 0.9 AND median_ms is within target
        (both conditions must hold to confirm mastery)

    Ordering: worst-first by (acc ascending, latency-over-target ratio descending).
    Families with no latency data (median_ms=None) or no target in skill_targets
    sort purely by acc (ratio treated as 0.0).

    Target derivation: primitives always use PRIMITIVE_ONSET_TARGET_MS=1200;
    compounds look up skill_targets by the family's originating skill (add→addition,
    sub→subtraction, mul→multiplication, div→division, money→money_arithmetic,
    weather→weather_math, pct→percent_of).
    """

    def _target(fam: str, tier: str) -> Optional[int]:
        if tier == "primitive":
            return PRIMITIVE_ONSET_TARGET_MS
        return skill_targets.get(_skill_for_family(fam))

    candidates: list[tuple[str, dict]] = []
    for fam, s in stats.items():
        if s["n"] < 5:
            continue
        tier = s["tier"]
        tgt = _target(fam, tier)
        acc = s["acc"]
        ms = s["median_ms"]
        # Mastered: acc>=0.9 AND we can confirm within target
        within = tgt is not None and ms is not None and ms <= tgt
        if acc >= 0.9 and within:
            continue
        candidates.append((fam, s))

    def _sort_key(item: tuple[str, dict]) -> tuple[float, float]:
        fam, s = item
        tgt = _target(fam, s["tier"])
        ms = s["median_ms"]
        ratio = (ms / tgt) if (tgt and ms is not None) else 0.0
        return (s["acc"], -ratio)

    candidates.sort(key=_sort_key)
    return [fam for fam, _ in candidates[:limit]]


def family_level(attempts: list[dict], family: str) -> int:
    """Compute the evidence-based level (1–3) for a family.

    Algorithm:
      1. Start at level 1.
      2. For each L in [1, 2] sequentially: if the last 10 of ≥10 attempts at
         level L have acc≥0.9 AND median latency within target, advance by 1
         (cap 3); else break.
      3. After advancement: if the last 10 attempts at the CURRENT level have
         acc<0.6, drop by 1 (floor 1).

    Target: PRIMITIVE_ONSET_TARGET_MS (1200ms) for primitives; for compound
    families the latency check is skipped (treated as always within target) since
    skill_targets is not available here.

    Level is read from parameters["level"]. Attempts for other families or without
    a parseable level are ignored.
    """
    # Collect attempts for this family, sorted oldest-first.
    # Derive tier authoritatively from classify() on the first matched attempt.
    tier: Optional[str] = None
    family_atts: list[tuple[dict, Optional[int]]] = []
    for a in attempts:
        if a.get("skipped"):
            continue
        params = _parse_params(a)
        taxon = classify(a.get("skill_id", ""), params)
        if taxon is None or taxon.family != family:
            continue
        if tier is None:
            tier = taxon.tier
        lv = params.get("level")
        family_atts.append((a, lv))

    # No classifiable attempts → return 1 (empty-input default, unchanged)
    if tier is None:
        return 1

    target: Optional[int] = PRIMITIVE_ONSET_TARGET_MS if tier == "primitive" else None

    family_atts.sort(key=lambda x: x[0].get("created_at", 0))

    def _within_target(lats: list[int]) -> bool:
        if target is None:
            return True  # compound: no target constraint
        return bool(lats) and statistics.median(lats) <= target

    def _level_passes(L: int) -> bool:
        """True if the last 10 of ≥10 attempts at level L meet the thresholds."""
        atts_at_L = [a for a, lv in family_atts if lv == L]
        if len(atts_at_L) < 10:
            return False
        last10 = atts_at_L[-10:]
        acc = sum(1 for a in last10 if a.get("correct")) / 10
        if acc < 0.9:
            return False
        lats = [lat for a in last10 for lat in [_latency(a, tier)] if lat is not None]
        return _within_target(lats)

    current = 1
    for L in [1, 2]:
        if _level_passes(L):
            current = min(3, current + 1)
        else:
            break

    # Drop check at current level
    atts_at_current = [a for a, lv in family_atts if lv == current]
    if len(atts_at_current) >= 10:
        last10 = atts_at_current[-10:]
        acc = sum(1 for a in last10 if a.get("correct")) / 10
        if acc < 0.6:
            current = max(1, current - 1)

    return current
