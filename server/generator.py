"""Per-skill problem generators.

Each function takes an explicit difficulty `level` (1=easy, 2=medium, 3=hard)
and an optional `target` dict for targeted drilling. When target is provided,
the generator uses those specific operands instead of random ones.

Returns:
    {"prompt": str, "expected": float, "parameters": dict}
"""

import logging
import random

from server import money, suppressions, weather

_log = logging.getLogger("feynman.suppressions")


def _compute_features(a: int, b: int, *, extra: dict | None = None) -> dict:
    f = {"abs_diff": abs(a - b), "min_operand": min(a, b), "max_operand": max(a, b)}
    if extra:
        f.update(extra)
    return f


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
            "features": _compute_features(a, b, extra={"has_carry": carry}),
        },
    }


def _sample_addition(level: int) -> tuple[int, int]:
    """Foundation-lane sampler. Caps stay inside 0-40 sums."""
    if level <= 1:
        for _ in range(20):
            a = random.randint(0, 9)
            b = random.randint(0, 9)
            if a + b <= 20:
                return a, b
        return 9, 9
    if level == 2:
        for _ in range(20):
            small = random.randint(1, 9)
            big = random.randint(10, 20)
            if small + big <= 30:
                return (small, big) if random.random() < 0.5 else (big, small)
        return 9, 20
    # level 3: both operands 10-20, sums <= 40
    for _ in range(20):
        a = random.randint(10, 20)
        b = random.randint(10, 20)
        if a + b <= 40:
            return a, b
    return 20, 20


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
    for _ in range(50):
        a = random.randint(*ranges[0])
        b = random.randint(*ranges[1]) if len(ranges) > 1 else random.randint(*ranges[0])
        if a + b > cap_sum:
            continue
        if force_carry and (a % 10) + (b % 10) < 10:
            continue
        if not force_carry and (a % 10) + (b % 10) >= 10:
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
            "features": _compute_features(a, b, extra={"has_borrow": borrow}),
        },
    }


def _sample_subtraction(level: int) -> tuple[int, int]:
    """Foundation-lane sampler. Stays inside 0-30 minuends."""
    if level <= 1:
        a = random.randint(5, 20)
        b = random.randint(0, min(9, a))
        return a, b
    if level == 2:
        # Force borrow practice within 0-20
        for _ in range(20):
            a = random.randint(11, 20)
            b = random.randint(2, min(12, a - 1))
            if b < 1:
                continue
            return a, b
        return 15, 8
    # level 3: minuend 20-30, subtrahend 5-20
    for _ in range(20):
        a = random.randint(20, 30)
        b = random.randint(5, min(20, a - 1))
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

    for _ in range(50):
        a = random.randint(*r_a)
        hi_b = min(r_b[1], a - 1) if r_a[0] >= 1 else r_b[1]
        lo_b = min(r_b[0], hi_b)
        if hi_b < lo_b:
            continue
        b = random.randint(lo_b, hi_b)
        if b < 0:
            continue
        has_borrow = (a % 10) < (b % 10)
        if force_borrow and not has_borrow:
            continue
        if not force_borrow and has_borrow:
            continue
        return a, b
    return a, max(0, b)


def _gen_multiplication(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    else:
        if level == 1:
            # 2/10/11-with-small-cofactor are suppressed (2026-07-02 rules);
            # keeping them here just burned the suppression retry budget and
            # let the give-up path emit trivial facts. L1 = easiest legit tables.
            easy_factors = [3, 4, 5]
            a = random.choice(easy_factors)
            b = random.randint(3, 12)
            if random.random() < 0.5:
                a, b = b, a
        elif level == 2:
            hard_factors = [3, 4, 6, 7, 8, 9]
            a = random.choice(hard_factors)
            b = random.choice(hard_factors)
        else:
            big_factors = [12, 15, 20]
            a = random.choice(big_factors)
            b = random.randint(2, 12)
            if random.random() < 0.5:
                a, b = b, a
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": _compute_features(a, b)},
    }


def _gen_division(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    else:
        # /1, /2 (small_operand) and /10, /20 (ten_divisor) are suppressed
        # (2026-07-02 rules) — dropped from the pools so they don't burn the
        # suppression retry budget. L1 = easiest legit divisors, mirroring
        # multiplication's easy_factors.
        divisors_l1 = [3, 4, 5]
        divisors_l2 = [3, 4, 6, 7, 8, 9]
        divisors_l3 = [11, 12, 15]
        pool = {1: divisors_l1, 2: divisors_l2, 3: divisors_l3}.get(level, divisors_l2)
        d = random.choice(pool)
        q = random.randint(2, 12)
        a, b = d * q, d
    return {
        "prompt": f"What is {a} divided by {b}?",
        "expected": float(a / b),
        "parameters": {"a": a, "b": b, "level": level,
                       "features": _compute_features(a, b)},
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
