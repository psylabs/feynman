"""Per-skill problem generators.

Each function takes an explicit difficulty `level` (1=easy, 2=medium, 3=hard)
and an optional `target` dict for targeted drilling. When target is provided,
the generator uses those specific operands instead of random ones.

Returns:
    {"prompt": str, "expected": float, "parameters": dict}
"""

import logging
import random

from server import bones, money, suppressions, weather

_log = logging.getLogger("feynman.suppressions")


def mastery_to_level(mastery: float) -> int:
    if mastery < 0.4:
        return 1
    if mastery < 0.75:
        return 2
    return 3


def _gen_addition(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    elif target and target.get("force_carry"):
        a, b = _addition_with_carry(level)
    elif target and target.get("pattern"):
        a, b = _addition_from_pattern(target["pattern"], target.get("force_carry", False))
    else:
        a, b = _sample_addition(level)
    carry = ((a % 10) + (b % 10)) >= 10
    return {
        "prompt": f"What is {a} plus {b}?",
        "expected": float(a + b),
        "parameters": {
            "a": a, "b": b, "carry": carry, "level": level,
            "features": bones.compute_features("+", (a, b)),
        },
    }


def _sample_addition(level: int) -> tuple[int, int]:
    """Foundation-lane sampler. Caps stay inside 0-40 sums."""
    active = suppressions.load_active()
    if level <= 1:
        for _ in range(20):
            a = random.randint(0, 9)
            b = random.randint(0, 9)
            if a + b > 20:
                continue
            params = {"a": a, "b": b, "features": bones.compute_features("+", (a, b))}
            if not suppressions.matches("addition", params, active):
                return a, b
        return 9, 9
    if level == 2:
        for _ in range(20):
            small = random.randint(1, 9)
            big = random.randint(10, 20)
            if small + big > 30:
                continue
            a, b = (small, big) if random.random() < 0.5 else (big, small)
            params = {"a": a, "b": b, "features": bones.compute_features("+", (a, b))}
            if not suppressions.matches("addition", params, active):
                return a, b
        return 9, 16
    # level 3: both operands 10-20, sums <= 40
    for _ in range(20):
        a = random.randint(10, 20)
        b = random.randint(10, 20)
        if a + b > 40:
            continue
        params = {"a": a, "b": b, "features": bones.compute_features("+", (a, b))}
        if not suppressions.matches("addition", params, active):
            return a, b
    return 17, 19


def _addition_with_carry(level: int) -> tuple[int, int]:
    """Generate an addition pair guaranteed to have a ones-digit carry,
    within the foundation-lane caps."""
    for _ in range(50):
        a, b = _sample_addition(level)
        if (a % 10) + (b % 10) >= 10:
            return a, b
    return a, b  # fallback


def _addition_from_pattern(pattern: str, force_carry: bool) -> tuple[int, int]:
    """Generate operands matching a digit-pattern like '2d+2d'.

    Capped to foundation-lane ranges. 3d patterns degrade to 2d-style operands;
    once `_infer_level_from_key` is updated the scheduler will stop requesting
    them, but this stays as a defensive cap.
    """
    parts = pattern.split("+")
    ranges = [_digit_range(p) for p in parts]
    cap_sum = 40
    # Two single-digit operands can only cross a ten via a carry, so the
    # no-carry preference is unsatisfiable there — crossing wins.
    can_cross_sans_carry = any(r[1] > 9 for r in ranges)
    active = suppressions.load_active()
    for _ in range(50):
        a = random.randint(*ranges[0])
        b = random.randint(*ranges[1]) if len(ranges) > 1 else random.randint(*ranges[0])
        if a + b > cap_sum:
            continue
        params = {"a": a, "b": b, "features": bones.compute_features("+", (a, b))}
        if suppressions.matches("addition", params, active):
            continue
        if force_carry and (a % 10) + (b % 10) < 10:
            continue
        if not force_carry and can_cross_sans_carry and (a % 10) + (b % 10) >= 10:
            continue
        return a, b
    return a, b


def _digit_range(spec: str) -> tuple[int, int]:
    """Convert '1d', '2d', '3d' to int ranges (capped to foundation lane)."""
    spec = spec.strip().lower()
    if spec == "1d":
        return (1, 9)
    if spec == "2d":
        return (10, 20)  # capped: foundation lane stays inside 0-20 operands
    if spec == "3d":
        return (10, 20)  # 3d patterns degrade to 2d ranges
    return (10, 20)


def _gen_subtraction(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
        if a < b:
            a, b = b, a
    elif target and (target.get("force_borrow") or target.get("pattern")):
        a, b = _subtraction_from_hints(level, target)
    else:
        a, b = _sample_subtraction(level)
    borrow = (a % 10) < (b % 10)
    return {
        "prompt": f"What is {a} minus {b}?",
        "expected": float(a - b),
        "parameters": {
            "a": a, "b": b, "borrow": borrow, "level": level,
            "features": bones.compute_features("-", (a, b)),
        },
    }


def _sample_subtraction(level: int) -> tuple[int, int]:
    """Foundation-lane sampler. Stays inside 0-30 minuends."""
    active = suppressions.load_active()
    if level <= 1:
        for _ in range(20):
            a = random.randint(5, 20)
            b = random.randint(0, min(9, a))
            params = {"a": a, "b": b, "features": bones.compute_features("-", (a, b))}
            if not suppressions.matches("subtraction", params, active):
                return a, b
        return 12, 5
    if level == 2:
        # Force borrow practice within 0-20
        for _ in range(20):
            a = random.randint(11, 20)
            b = random.randint(2, min(12, a - 1))
            if b < 1:
                continue
            params = {"a": a, "b": b, "features": bones.compute_features("-", (a, b))}
            if not suppressions.matches("subtraction", params, active):
                return a, b
        return 15, 8
    # level 3: minuend 20-30, subtrahend 5-20
    for _ in range(20):
        a = random.randint(20, 30)
        b = random.randint(5, min(20, a - 1))
        params = {"a": a, "b": b, "features": bones.compute_features("-", (a, b))}
        if not suppressions.matches("subtraction", params, active):
            return a, b
    return 25, 12


def _subtraction_from_hints(level: int, target: dict) -> tuple[int, int]:
    """Generate subtraction operands from pattern hints, capped to 0-30."""
    force_borrow = target.get("force_borrow", False)
    pattern = target.get("pattern", "")
    parts = pattern.split("-") if pattern else []

    if len(parts) == 2:
        r_a = _digit_range(parts[0])
        r_b = _digit_range(parts[1])
        # Tighten: minuend max 30, subtrahend max 20
        r_a = (max(2, r_a[0]), min(30, r_a[1]))
        r_b = (max(0, r_b[0]), min(20, r_b[1]))
    elif level <= 1:
        r_a, r_b = (5, 20), (0, 9)
    elif level == 2:
        r_a, r_b = (11, 20), (2, 12)
    else:
        r_a, r_b = (20, 30), (5, 20)

    # A single-digit subtrahend can only cross a ten via a borrow, so the
    # no-borrow preference is unsatisfiable there — crossing wins.
    can_cross_sans_borrow = r_b[1] >= 10
    active = suppressions.load_active()
    for _ in range(50):
        a = random.randint(*r_a)
        hi_b = min(r_b[1], a - 1) if r_a[0] >= 1 else r_b[1]
        lo_b = min(r_b[0], hi_b)
        if hi_b < lo_b:
            continue
        b = random.randint(lo_b, hi_b)
        if b < 0:
            continue
        params = {"a": a, "b": b, "features": bones.compute_features("-", (a, b))}
        if suppressions.matches("subtraction", params, active):
            continue
        has_borrow = (a % 10) < (b % 10)
        if force_borrow and not has_borrow:
            continue
        if not force_borrow and can_cross_sans_borrow and has_borrow:
            continue
        return a, b
    return a, max(0, b)


def _gen_multiplication(level: int, target: dict | None = None) -> dict:
    """Pools raised to the 13-19 tables (2026-07-05 doctrine: single-digit and
    10/11/12/15/20 tables are retired — too_easy, flagged dozens of times).
    Every level pairs at least one operand from 13-19 with a "table row"
    partner (6-9, or 12-19 at L3), which always clears the active
    suppression set (max_operand>=13, trivial_value, by_ten) — see
    tests/test_suppressions.py::RaisedFloorPoolTests.
    """
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    else:
        if level == 1:
            a = random.choice([13, 14, 15])
            b = random.choice([6, 7, 8, 9])
        elif level == 2:
            a = random.choice(range(13, 20))
            b = random.choice([6, 7, 8, 9])
        else:
            a = random.choice(range(13, 20))
            b = random.choice(range(12, 20))
        if random.random() < 0.5:
            a, b = b, a
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": bones.compute_features("*", (a, b))},
    }


def _gen_division(level: int, target: dict | None = None) -> dict:
    """Pools raised to the 13-19 tables (2026-07-05 doctrine), mirroring
    multiplication. ``d`` (divisor) and ``q`` (quotient) are the two recalled
    facts; every level pairs a 6-9/12 partner with a 13-19 partner, which
    always clears the active suppression set (max_operand>=13 on the
    (divisor, quotient) feature pair per Task 3, trivial_value, ten_divisor).
    """
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    else:
        if level == 1:
            d = random.choice([6, 7, 8, 9])
            q = random.choice([13, 14, 15])
        elif level == 2:
            d = random.choice([6, 7, 8, 9, 12])
            q = random.choice(range(13, 20))
        else:
            d = random.choice(range(13, 20))
            q = random.choice([6, 7, 8, 9, 12])
        a, b = d * q, d
    return {
        "prompt": f"What is {a} divided by {b}?",
        "expected": float(a / b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": bones.compute_features("/", (b, a // b))},
    }


def _gen_percent_of(level: int, target: dict | None = None) -> dict:
    # 10% (a pure decimal shift) and base 100 are trivial by user rule
    # (2026-07-02 recalibration + "percentages of 100 are trivial" feedback) —
    # neither appears in any pool below.
    if target and target.get("percentage") is not None:
        pct = int(target["percentage"])
        # Pick a base appropriate for the level
        if level == 1:
            base = random.choice([40, 60, 80, 120, 200, 50, 250])
        elif level == 2:
            base = random.choice([60, 80, 120, 150, 200, 75, 145])
        else:
            base = random.choice([137, 175, 240, 95, 165, 285])
    else:
        if level == 1:
            pct = random.choice([5, 20, 50])
            base = random.choice([40, 60, 80, 120, 200, 50, 250])
        elif level == 2:
            if random.random() < 0.5:
                pct = random.choice([15, 25, 18])
                base = random.choice([60, 80, 120, 200, 150])
            else:
                pct = random.choice([5, 20, 50])
                base = random.choice([75, 85, 145, 165, 230])
        else:
            pct = random.choice([15, 18, 25])
            base = random.choice([137, 175, 240, 95, 165, 285])
    expected = pct * base / 100.0
    return {
        "prompt": f"What is {pct} percent of {base}?",
        "expected": expected,
        "parameters": {"percentage": pct, "base": base, "level": level},
    }


def _gen_money_arithmetic(level: int, target: dict | None = None) -> dict:
    problem = money.generate_problem(target=target)
    problem["parameters"]["level"] = level
    return problem


def _gen_weather_math(level: int, target: dict | None = None) -> dict:
    problem = weather.generate_problem(target=target)
    problem["parameters"]["level"] = level
    return problem


GENERATORS = {
    "addition": _gen_addition,
    "subtraction": _gen_subtraction,
    "multiplication": _gen_multiplication,
    "division": _gen_division,
    "percent_of": _gen_percent_of,
    "money_arithmetic": _gen_money_arithmetic,
    "weather_math": _gen_weather_math,
}


def generate(
    skill_id: str,
    level: int | None = None,
    mastery: float | None = None,
    target: dict | None = None,
) -> dict:
    """Dispatch to the per-skill generator and return one rendered problem.

    Each entry in ``GENERATORS`` is a ``(level, target) -> dict`` callable
    that returns ``{prompt_text, expected_answer, parameters}``. ``level`` is
    derived from ``mastery`` via ``mastery_to_level`` when not supplied, then
    clamped to [1, 3]. Passing ``target`` lets the caller pin a specific fact
    (e.g. ``{"a": 6, "b": 10}`` for multiplication).

    Every result is checked against the active suppression rules from
    ``suppressions.yaml`` (see ``server.suppressions``). If a sampled
    result matches a rule, ``target`` is dropped and we re-sample freely
    until we get a non-trivial problem. The user's suppression list wins
    over scheduler hints — the scheduler will get a different problem in
    the same skill.

    Raises ``ValueError`` for unknown ``skill_id`` (the dispatch is closed —
    new skills require a corresponding generator function).
    """
    fn = GENERATORS.get(skill_id)
    if not fn:
        raise ValueError(f"no generator for skill: {skill_id}")
    if level is None:
        level = mastery_to_level(mastery if mastery is not None else 0.5)
    level = max(1, min(3, int(level)))

    active = suppressions.load_active()
    result = fn(level, target)
    if not suppressions.matches(skill_id, result.get("parameters", {}), active):
        return result

    # Sampled result is trivial. Drop the target hint and re-sample.
    for _ in range(suppressions.MAX_RETRIES):
        result = fn(level, None)
        if not suppressions.matches(skill_id, result.get("parameters", {}), active):
            return result
    _log.warning(
        "suppression.give_up skill_id=%s retries=%d", skill_id, suppressions.MAX_RETRIES
    )
    return result
