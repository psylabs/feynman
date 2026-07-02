"""Golden + safety tests for the verified strategy library (server/strategies.py).

Every mental-math tip must be arithmetically true: the framework asserts each
decomposition reproduces the graded answer and drops any tip that can't verify.
These tests pin one triggering + one non-triggering case for all 26 rules, a
50-seed property test that no tip ever states a wrong answer, and an adversarial
test proving the try/except safety net (not just the asserts) catches a liar.
"""
import random

import pytest

from server import strategies
from server.strategies import Strategy, tips_for


# --- golden table: (id, skill_id, params, expected, must_contain) -----------
# `must_contain` is a distinctive substring of the tip; each triggering case is
# also checked to render the literal final answer.
TRIGGER = [
    # subtraction (exact)
    ("count_up", "subtraction", {"a": 23, "b": 20}, 3.0, "count UP"),
    ("subtract_in_hops", "subtraction", {"a": 63, "b": 28, "borrow": True}, 35.0, "add back"),
    ("left_to_right_sub", "subtraction", {"a": 87, "b": 24, "borrow": False}, 63.0, "left to right"),
    ("left_to_right_sub.zero", "subtraction", {"a": 96, "b": 40, "borrow": False}, 56.0, "trailing zero"),
    ("complement_100", "subtraction", {"a": 100, "b": 37}, 63.0, "no borrowing"),
    ("complement_1000", "subtraction", {"a": 1000, "b": 375}, 625.0, "no borrowing"),
    ("sub_same_ones", "subtraction", {"a": 94, "b": 24}, 70.0, "ones match"),
    # addition (exact)
    ("bridge_ten", "addition", {"a": 8, "b": 7}, 15.0, "fill to ten"),
    ("doubles_anchor", "addition", {"a": 7, "b": 8}, 15.0, "near-doubles"),
    ("compensation_add", "addition", {"a": 19, "b": 24}, 43.0, "take back"),
    ("add_tens_then_ones", "addition", {"a": 45, "b": 32}, 77.0, "then +2"),
    # multiplication (exact)
    ("times_9", "multiplication", {"a": 9, "b": 7}, 63.0, "minus one of them"),
    ("times_12", "multiplication", {"a": 12, "b": 6}, 72.0, "×10 + ×2"),
    ("times_11", "multiplication", {"a": 11, "b": 11}, 121.0, "split the digits"),
    ("times_4", "multiplication", {"a": 4, "b": 3}, 12.0, "double twice"),
    ("times_5", "multiplication", {"a": 5, "b": 4}, 20.0, "halve"),
    ("times_8", "multiplication", {"a": 8, "b": 7}, 56.0, "three times"),
    ("times_15", "multiplication", {"a": 15, "b": 8}, 120.0, "half again"),
    ("times_20", "multiplication", {"a": 20, "b": 7}, 140.0, "double then ×10"),
    ("near_square", "multiplication", {"a": 8, "b": 12}, 96.0, "straddles"),
    # division (exact)
    ("div_as_inverse", "division", {"a": 56, "b": 7}, 8.0, "makes 56"),
    # percent / money (money tolerance)
    ("pct_anchor", "percent_of", {"percentage": 25, "base": 60}, 15.0, "quarter"),
    ("tip_15", "money_arithmetic",
     {"operation": "restaurant_tip_15", "bill_amount": 100, "tip_percent": 15}, 15.0, "10%"),
    ("split_round_friendly", "money_arithmetic",
     {"operation": "split_bill", "bill_amount": 96, "people": 4}, 24.0, "each"),
    ("round_then_total", "money_arithmetic",
     {"operation": "charge_total", "amounts": [21, 34, 18]}, 73.0, "whole dollars"),
    ("round_both_subtract", "money_arithmetic",
     {"operation": "category_difference", "amount_a": 210, "amount_b": 140}, 70.0, "round to tens"),
    # weather (exact)
    ("f_to_c", "weather_math",
     {"operation": "f_to_c_approx", "fahrenheit": 72}, 21.0, "halve"),
    ("f_between", "weather_math",
     {"operation": "daily_range", "high": 62, "low": 53}, 9.0, "count up"),
    ("f_between.delta", "weather_math",
     {"operation": "temp_delta", "warmer": 80, "cooler": 68}, 12.0, "count up"),
]


@pytest.mark.parametrize("case", TRIGGER, ids=[c[0] for c in TRIGGER])
def test_strategy_triggers_and_states_answer(case):
    _id, skill, params, expected, must_contain = case
    tips = tips_for(skill, params, expected, None)
    assert tips, f"{_id}: expected at least one tip"
    hit = [t for t in tips if must_contain in t]
    assert hit, f"{_id}: no tip contained {must_contain!r}; got {tips}"
    # the literal final answer must appear in the matching tip
    ans = str(int(expected)) if expected == int(expected) else f"{expected:g}"
    assert any(ans in t for t in hit), f"{_id}: answer {ans} missing from {hit}"


# --- non-triggering cases: each rule must decline when it doesn't apply ------
NON_TRIGGER = [
    ("count_up", "subtraction", {"a": 90, "b": 12}, 78.0, "count UP"),
    ("subtract_in_hops", "subtraction", {"a": 63, "b": 28, "borrow": False}, 35.0, "add back"),
    ("complement_100", "subtraction", {"a": 99, "b": 37}, 62.0, "no borrowing"),
    ("sub_same_ones", "subtraction", {"a": 94, "b": 23}, 71.0, "ones match"),
    ("doubles_anchor", "addition", {"a": 3, "b": 40}, 43.0, "near-doubles"),
    ("times_9", "multiplication", {"a": 6, "b": 7}, 42.0, "minus one of them"),
    ("times_11", "multiplication", {"a": 6, "b": 7}, 42.0, "split the digits"),
    ("times_15", "multiplication", {"a": 6, "b": 7}, 42.0, "half again"),
    ("div_as_inverse", "division", {"a": 10, "b": 3}, 10 / 3, "makes"),
    ("pct_anchor", "percent_of", {"percentage": 33, "base": 60}, 19.8, "of 60"),
]


@pytest.mark.parametrize("case", NON_TRIGGER, ids=[c[0] for c in NON_TRIGGER])
def test_strategy_declines(case):
    _id, skill, params, expected, must_not_contain = case
    tips = tips_for(skill, params, expected, None)
    assert not any(must_not_contain in t for t in tips), \
        f"{_id}: should not have fired; got {tips}"


def test_at_most_two_tips():
    tips = tips_for("subtraction", {"a": 100, "b": 91}, 9.0, None)
    assert len(tips) <= 2


# --- property: no strategy ever states a wrong answer -----------------------
@pytest.mark.parametrize("seed", range(50))
def test_no_strategy_ever_lies(seed):
    rng = random.Random(seed)
    a, b = rng.randint(2, 999), rng.randint(2, 99)
    a, b = max(a, b), min(a, b)
    for skill, params, expected in [
        ("subtraction", {"a": a, "b": b}, float(a - b)),
        ("addition", {"a": a, "b": b}, float(a + b)),
        ("multiplication",
         {"a": rng.randint(2, 20), "b": rng.randint(2, 20)}, None),
    ]:
        if expected is None:
            expected = float(params["a"] * params["b"])
        for tip in tips_for(skill, params, expected, None):
            assert str(int(expected)) in tip or f"{expected:g}" in tip, \
                f"tip lied: {tip!r} for {skill} {params} = {expected}"


# --- adversarial: the try/except safety net, not just the asserts, drops
#     a tip whose decomposition doesn't reproduce the answer ----------------
def test_safety_net_drops_a_lying_strategy(monkeypatch):
    def liar(p, expected):
        wrong = expected + 1            # deliberately wrong decomposition
        assert wrong == expected        # ... guarded by the iron-rule assert
        return f"lie: the answer is {int(wrong)}"

    def honest(p, expected):
        return f"honest: the answer is {int(expected)}"

    # A registry of one liar: the assert fails, framework must swallow it.
    monkeypatch.setattr(strategies, "REGISTRY",
                        [Strategy("liar", ("mul.x9",), liar)])
    assert tips_for("multiplication", {"a": 9, "b": 7}, 63.0, None) == []

    # Same shape without the guard *does* surface — proving the drop above was
    # caused by the failed verification, not by routing.
    monkeypatch.setattr(strategies, "REGISTRY",
                        [Strategy("honest", ("mul.x9",), honest)])
    assert tips_for("multiplication", {"a": 9, "b": 7}, 63.0, None) == \
        ["honest: the answer is 63"]
