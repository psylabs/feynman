"""Tests for server/family_stats.py — TDD, all synthetic attempts.

Attempt dicts match storage.all_attempts_for_user output:
  parameters already parsed as dict (not JSON string).
  Fields: skill_id, parameters, correct, skipped, onset_latency_ms,
  resolution_latency_ms, created_at.

Family-producing parameter examples (verified via taxonomy.classify):
  sub.3d-2d.bt  → subtraction a=565, b=85   (tens borrow, no ones borrow)
  sub.within20  → subtraction a=18, b=4
  add.within20  → addition a=4, b=4
  add.bridge10  → addition a=8, b=5
  mul.x12       → multiplication a=9, b=12   (primitive)
  add.2d+2d.cot → addition a=87, b=96        (compound, carry in ones+tens)
"""

import pytest
from server.taxonomy import classify

# ---------------------------------------------------------------------------
# Synthetic-attempt helpers
# ---------------------------------------------------------------------------

def _attempt(skill_id, params, *, correct=True, skipped=False,
              onset_ms=800, resolution_ms=1500, created_at=0.0):
    """Build a synthetic attempt dict with all fields family_stats needs."""
    return {
        "skill_id": skill_id,
        "parameters": params,
        "correct": correct,
        "skipped": skipped,
        "onset_latency_ms": onset_ms,
        "resolution_latency_ms": resolution_ms,
        "created_at": created_at,
    }


def _make_attempts(skill_id, params, n, *, correct=True, onset_ms=800,
                   resolution_ms=1500, start_ts=0.0, level=None):
    """Fabricate n attempts for a single family."""
    p = dict(params)
    if level is not None:
        p["level"] = level
    return [
        _attempt(skill_id, p, correct=correct, onset_ms=onset_ms,
                 resolution_ms=resolution_ms, created_at=start_ts + i)
        for i in range(n)
    ]


# Verify the params produce the expected family (fail-fast guard)
def test_params_produce_expected_families():
    assert classify("subtraction", {"a": 565, "b": 85}).family == "sub.3d-2d.bt"
    assert classify("subtraction", {"a": 18, "b": 4}).family == "sub.within20"
    assert classify("addition", {"a": 4, "b": 4}).family == "add.within20"
    assert classify("addition", {"a": 8, "b": 5}).family == "add.bridge10"
    assert classify("multiplication", {"a": 9, "b": 12}).family == "mul.x12"
    assert classify("addition", {"a": 87, "b": 96}).family == "add.2d+2d.cot"


# ---------------------------------------------------------------------------
# family_stats
# ---------------------------------------------------------------------------

from server.family_stats import family_stats, weak_families, family_level


def test_family_stats_basic():
    """family_stats groups attempts by family, computes n/acc/median/tier."""
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 5,
                          correct=True, resolution_ms=1200)
    stats = family_stats(atts)
    assert "sub.3d-2d.bt" in stats
    s = stats["sub.3d-2d.bt"]
    assert s["tier"] == "compound"
    assert s["n"] == 5
    assert s["acc"] == 1.0
    assert s["median_ms"] == 1200


def test_family_stats_skips_excluded():
    """Attempts with skipped=True must be excluded from family_stats."""
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 3, correct=True)
    atts += [_attempt("subtraction", {"a": 565, "b": 85}, correct=False, skipped=True)]
    stats = family_stats(atts)
    s = stats["sub.3d-2d.bt"]
    assert s["n"] == 3  # skipped attempt not counted


def test_family_stats_primitive_uses_onset_latency():
    """Primitive families use onset_latency_ms, not resolution_latency_ms."""
    # mul.x12 is primitive
    atts = _make_attempts("multiplication", {"a": 9, "b": 12}, 5,
                          correct=True, onset_ms=700, resolution_ms=9999)
    stats = family_stats(atts)
    s = stats["mul.x12"]
    assert s["tier"] == "primitive"
    assert s["median_ms"] == 700  # onset, not 9999


def test_family_stats_compound_uses_resolution_latency():
    """Compound families use resolution_latency_ms."""
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 5,
                          correct=True, onset_ms=300, resolution_ms=1800)
    stats = family_stats(atts)
    s = stats["sub.3d-2d.bt"]
    assert s["median_ms"] == 1800  # resolution, not 300


def test_family_stats_median_correct_only():
    """median_ms is computed over CORRECT attempts only."""
    # 3 correct at 1000ms resolution, 2 incorrect at 9000ms resolution
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 3,
                          correct=True, resolution_ms=1000)
    atts += _make_attempts("subtraction", {"a": 565, "b": 85}, 2,
                           correct=False, resolution_ms=9000)
    stats = family_stats(atts)
    s = stats["sub.3d-2d.bt"]
    assert s["n"] == 5
    assert s["acc"] == pytest.approx(0.6, abs=0.01)
    assert s["median_ms"] == 1000  # not 9000


def test_family_stats_no_latency_when_no_correct():
    """median_ms is None when there are no correct attempts."""
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 4, correct=False)
    stats = family_stats(atts)
    assert stats["sub.3d-2d.bt"]["median_ms"] is None


def test_family_stats_last_seen_at():
    """last_seen_at is the max created_at across family attempts."""
    atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 3,
                          correct=True, start_ts=100.0)
    # created_at will be 100.0, 101.0, 102.0
    stats = family_stats(atts)
    assert stats["sub.3d-2d.bt"]["last_seen_at"] == pytest.approx(102.0)


def test_family_stats_multiple_families():
    """Attempts from different families are bucketed separately."""
    sub_atts = _make_attempts("subtraction", {"a": 565, "b": 85}, 3, correct=True)
    add_atts = _make_attempts("addition", {"a": 4, "b": 4}, 4, correct=True)
    stats = family_stats(sub_atts + add_atts)
    assert "sub.3d-2d.bt" in stats
    assert "add.within20" in stats
    assert stats["sub.3d-2d.bt"]["n"] == 3
    assert stats["add.within20"]["n"] == 4


def test_family_stats_ignores_unclassifiable():
    """Attempts with unknown skill_id or missing params produce no family entry."""
    atts = [_attempt("unknown_skill", {"x": 1}, correct=True)]
    stats = family_stats(atts)
    assert len(stats) == 0


def test_family_stats_params_as_json_string():
    """family_stats handles parameters as a JSON string (storage sqlite3.Row fallback)."""
    import json
    a = _attempt("subtraction", json.dumps({"a": 565, "b": 85}), correct=True,
                 resolution_ms=1500)
    stats = family_stats([a])
    assert "sub.3d-2d.bt" in stats


# ---------------------------------------------------------------------------
# weak_families
# ---------------------------------------------------------------------------

def _make_stats(family, tier, n, acc, median_ms):
    return {family: {"tier": tier, "n": n, "acc": acc, "median_ms": median_ms,
                     "last_seen_at": 0.0}}


SKILL_TARGETS = {
    "subtraction": 3000,
    "addition": 2500,
    "multiplication": 2000,
    "division": 2000,
    "money_arithmetic": 4000,
    "weather_math": 4000,
    "percent_of": 3500,
}


def test_weak_families_excludes_mastered():
    """Family with acc>=0.9 AND within target is excluded."""
    stats = _make_stats("sub.3d-2d.bt", "compound", 10, 0.95, 2000)
    result = weak_families(stats, SKILL_TARGETS)
    assert "sub.3d-2d.bt" not in result


def test_weak_families_includes_slow_accurate():
    """Family with acc>=0.9 but ABOVE target latency is included."""
    stats = _make_stats("sub.3d-2d.bt", "compound", 10, 0.95, 4000)
    result = weak_families(stats, SKILL_TARGETS)
    assert "sub.3d-2d.bt" in result


def test_weak_families_excludes_low_n():
    """Families with n<5 are excluded from weak_families."""
    stats = _make_stats("sub.3d-2d.bt", "compound", 4, 0.3, 5000)
    result = weak_families(stats, SKILL_TARGETS)
    assert "sub.3d-2d.bt" not in result


def test_weak_families_ordering_acc_before_slow():
    """0.45-acc family ranks before slow-but-accurate (0.92-acc, above-target) family."""
    stats = {}
    # very inaccurate, within-target latency
    stats["sub.3d-2d.bt"] = {"tier": "compound", "n": 10, "acc": 0.45,
                              "median_ms": 2500, "last_seen_at": 0.0}
    # accurate but slow (above target)
    stats["add.2d+2d.cot"] = {"tier": "compound", "n": 10, "acc": 0.92,
                               "median_ms": 3500, "last_seen_at": 0.0}
    result = weak_families(stats, SKILL_TARGETS)
    # inaccurate family must come first (acc 0.45 < 0.92)
    assert result[0] == "sub.3d-2d.bt"
    assert result[1] == "add.2d+2d.cot"


def test_weak_families_primitive_uses_primitive_target():
    """Primitive families use PRIMITIVE_ONSET_TARGET_MS=1200, not skill_targets."""
    # mul.x12 is primitive; median_ms 1100 < 1200 → within target → if acc>=0.9 → excluded
    stats = _make_stats("mul.x12", "primitive", 10, 0.95, 1100)
    result = weak_families(stats, SKILL_TARGETS)
    assert "mul.x12" not in result

    # median_ms 1400 > 1200 → above target → included even with acc=0.95
    stats = _make_stats("mul.x12", "primitive", 10, 0.95, 1400)
    result = weak_families(stats, SKILL_TARGETS)
    assert "mul.x12" in result


def test_weak_families_limit():
    """weak_families respects the limit parameter."""
    stats = {}
    for i in range(20):
        stats[f"sub.{i}"] = {"tier": "compound", "n": 10, "acc": 0.3,
                              "median_ms": 5000, "last_seen_at": 0.0}
    result = weak_families(stats, SKILL_TARGETS, limit=5)
    assert len(result) == 5


def test_weak_families_money_weather_pct_targets():
    """money./weather./pct families use their own skill targets."""
    # money.split_bill: target=4000, median=5000 → above target → included despite acc=0.95
    stats = _make_stats("money.split_bill", "compound", 10, 0.95, 5000)
    result = weak_families(stats, SKILL_TARGETS)
    assert "money.split_bill" in result

    # pct: target=3500, median=3000 → within target → excluded if acc=0.95
    stats = _make_stats("pct", "compound", 10, 0.95, 3000)
    result = weak_families(stats, SKILL_TARGETS)
    assert "pct" not in result


# ---------------------------------------------------------------------------
# family_level
# ---------------------------------------------------------------------------

_SUB_PARAMS = {"a": 565, "b": 85}  # → sub.3d-2d.bt (compound)
_MUL_PARAMS = {"a": 9, "b": 12}    # → mul.x12 (primitive)


def test_family_level_defaults_to_1():
    """With no attempts, family_level returns 1."""
    assert family_level([], "sub.3d-2d.bt") == 1


def test_family_level_advances_on_10_strong_l1():
    """10+ strong level-1 attempts → advance to level 2."""
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=True, resolution_ms=1500, level=1)
    assert family_level(atts, "sub.3d-2d.bt") == 2


def test_family_level_no_advance_on_fewer_than_10():
    """Fewer than 10 level-1 attempts → stay at 1."""
    atts = _make_attempts("subtraction", _SUB_PARAMS, 9,
                          correct=True, resolution_ms=1500, level=1)
    assert family_level(atts, "sub.3d-2d.bt") == 1


def test_family_level_advances_to_3_on_l1_and_l2():
    """10+ strong level-1 AND 10+ strong level-2 attempts → advance to 3."""
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=True, resolution_ms=1500, level=1)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 10,
                           correct=True, resolution_ms=1500, level=2,
                           start_ts=20.0)
    assert family_level(atts, "sub.3d-2d.bt") == 3


def test_family_level_caps_at_3():
    """Even with "level 3" mastered, level is capped at 3."""
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=True, resolution_ms=1500, level=1)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 10,
                           correct=True, resolution_ms=1500, level=2, start_ts=20.0)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 10,
                           correct=True, resolution_ms=1500, level=3, start_ts=40.0)
    assert family_level(atts, "sub.3d-2d.bt") == 3


def test_family_level_drops_on_10_weak_at_current():
    """10 weak (acc<0.6) attempts at current level → drop 1, floor 1."""
    # 10 strong L1 → advance to 2; then 10 weak at L2 → drop back to 1
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=True, resolution_ms=1500, level=1)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 10,
                           correct=False, resolution_ms=4000, level=2,
                           start_ts=20.0)
    # 5/10 correct = 0.5 < 0.6 → drop from 2 to 1
    # Wait, all 10 incorrect → 0/10 = 0.0 < 0.6
    assert family_level(atts, "sub.3d-2d.bt") == 1


def test_family_level_no_drop_on_acc_0_6():
    """Exactly 0.6 accuracy at current level: no drop (threshold is <0.6)."""
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=True, resolution_ms=1500, level=1)
    # 6 correct, 4 incorrect at L2 = 0.6 acc → exactly 0.6, no drop
    atts += _make_attempts("subtraction", _SUB_PARAMS, 6,
                           correct=True, resolution_ms=2000, level=2, start_ts=20.0)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 4,
                           correct=False, resolution_ms=4000, level=2, start_ts=30.0)
    assert family_level(atts, "sub.3d-2d.bt") == 2


def test_family_level_floor_at_1():
    """Drop doesn't go below 1."""
    # No L1 mastery, 10 weak at L1 → stay at 1 (already floor, no drop to 0)
    atts = _make_attempts("subtraction", _SUB_PARAMS, 10,
                          correct=False, resolution_ms=5000, level=1)
    assert family_level(atts, "sub.3d-2d.bt") == 1


def test_family_level_primitive_uses_onset_for_advance():
    """Primitive family advance uses onset_latency_ms vs PRIMITIVE_ONSET_TARGET_MS=1200."""
    # mul.x12 is primitive; onset_ms=1100 < 1200 → within target → advance
    atts = _make_attempts("multiplication", _MUL_PARAMS, 10,
                          correct=True, onset_ms=1100, level=1)
    assert family_level(atts, "mul.x12") == 2


def test_family_level_primitive_no_advance_if_slow():
    """Primitive family doesn't advance if onset latency exceeds 1200ms target."""
    atts = _make_attempts("multiplication", _MUL_PARAMS, 10,
                          correct=True, onset_ms=1500, level=1)  # 1500 > 1200
    assert family_level(atts, "mul.x12") == 1


def test_family_level_ignores_other_families():
    """Attempts for other families don't count toward the target family level."""
    # These are add.within20 attempts, not sub.3d-2d.bt
    atts = _make_attempts("addition", {"a": 4, "b": 4}, 10,
                          correct=True, resolution_ms=1500, level=1)
    assert family_level(atts, "sub.3d-2d.bt") == 1


def test_family_level_l1_fail_blocks_l2_advance():
    """If level-1 doesn't pass, level-2 mastery doesn't advance us above 1."""
    # L1 has only 5 attempts (not enough) → stays at 1 even with 10 good L2
    atts = _make_attempts("subtraction", _SUB_PARAMS, 5,
                          correct=True, resolution_ms=1500, level=1)
    atts += _make_attempts("subtraction", _SUB_PARAMS, 10,
                           correct=True, resolution_ms=1500, level=2, start_ts=10.0)
    assert family_level(atts, "sub.3d-2d.bt") == 1
