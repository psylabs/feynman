"""Per-skill problem generators.

Each function takes an explicit difficulty `level` (1=easy, 2=medium, 3=hard)
and an optional `target` dict for targeted drilling. When target is provided,
the generator uses those specific operands instead of random ones.

Returns:
    {"prompt": str, "expected": float, "parameters": dict}
"""

import random

from server import money


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
        if level == 1:
            # Single-digit and small sums (up to 20)
            a = random.randint(2, 9)
            b = random.randint(2, 9)
        elif level == 2:
            # Two-digit, mix of carry and no-carry
            a = random.randint(12, 99)
            b = random.randint(12, 99)
        else:
            a = random.randint(100, 999)
            b = random.randint(100, 999)
    return {
        "prompt": f"What is {a} plus {b}?",
        "expected": float(a + b),
        "parameters": {
            "a": a,
            "b": b,
            "carry": ((a % 10) + (b % 10)) >= 10,
            "level": level,
        },
    }


def _addition_with_carry(level: int) -> tuple[int, int]:
    """Generate an addition pair guaranteed to have a ones-digit carry."""
    for _ in range(50):
        if level <= 1:
            a, b = random.randint(2, 9), random.randint(2, 9)
        elif level == 2:
            a, b = random.randint(15, 99), random.randint(15, 99)
        else:
            a, b = random.randint(100, 999), random.randint(100, 999)
        if (a % 10) + (b % 10) >= 10:
            return a, b
    return a, b  # fallback


def _addition_from_pattern(pattern: str, force_carry: bool) -> tuple[int, int]:
    """Generate operands matching a digit-pattern like '2d+2d'."""
    parts = pattern.split("+")
    ranges = [_digit_range(p) for p in parts]
    for _ in range(50):
        a = random.randint(*ranges[0])
        b = random.randint(*ranges[1]) if len(ranges) > 1 else random.randint(*ranges[0])
        if force_carry and (a % 10) + (b % 10) < 10:
            continue
        if not force_carry and (a % 10) + (b % 10) >= 10:
            continue
        return a, b
    return a, b


def _digit_range(spec: str) -> tuple[int, int]:
    """Convert '1d', '2d', '3d' to int ranges."""
    spec = spec.strip().lower()
    if spec == "1d":
        return (1, 9)
    if spec == "2d":
        return (10, 99)
    if spec == "3d":
        return (100, 999)
    return (10, 99)


def _gen_subtraction(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
        if a < b:
            a, b = b, a
    elif target and (target.get("force_borrow") or target.get("pattern")):
        a, b = _subtraction_from_hints(level, target)
    else:
        if level == 1:
            # Single-digit subtraction and teens minus single digit
            a = random.randint(5, 18)
            b = random.randint(2, min(9, a - 1))
        elif level == 2:
            a = random.randint(20, 99)
            b = random.randint(5, a - 1)
        else:
            a = random.randint(100, 999)
            b = random.randint(20, 99)
    return {
        "prompt": f"What is {a} minus {b}?",
        "expected": float(a - b),
        "parameters": {
            "a": a,
            "b": b,
            "borrow": (a % 10) < (b % 10),
            "level": level,
        },
    }


def _subtraction_from_hints(level: int, target: dict) -> tuple[int, int]:
    """Generate subtraction operands from pattern hints."""
    force_borrow = target.get("force_borrow", False)
    pattern = target.get("pattern", "")
    parts = pattern.split("-") if pattern else []

    if len(parts) == 2:
        r_a = _digit_range(parts[0])
        r_b = _digit_range(parts[1])
    elif level <= 1:
        r_a, r_b = (20, 99), (1, 49)
    elif level == 2:
        r_a, r_b = (30, 99), (15, 98)
    else:
        r_a, r_b = (100, 999), (20, 99)

    for _ in range(50):
        a = random.randint(*r_a)
        b = random.randint(min(r_b[0], a - 1), min(r_b[1], a - 1))
        if b < 1:
            continue
        has_borrow = (a % 10) < (b % 10)
        if force_borrow and not has_borrow:
            continue
        if not force_borrow and has_borrow:
            continue
        return a, b
    return a, max(1, b)


def _gen_multiplication(level: int, target: dict | None = None) -> dict:
    if target and target.get("a") is not None and target.get("b") is not None:
        a, b = int(target["a"]), int(target["b"])
    else:
        if level == 1:
            easy_factors = [2, 5, 10]
            a = random.choice(easy_factors)
            b = random.randint(2, 12)
            if random.random() < 0.5:
                a, b = b, a
        elif level == 2:
            hard_factors = [6, 7, 8, 9, 11, 12]
            a = random.choice(hard_factors)
            b = random.choice(hard_factors)
        else:
            a = random.randint(13, 49)
            b = random.randint(3, 9)
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "level": level},
    }


def _gen_percent_of(level: int, target: dict | None = None) -> dict:
    if target and target.get("percentage") is not None:
        pct = int(target["percentage"])
        # Pick a base appropriate for the level
        if level == 1:
            base = random.choice([40, 60, 80, 100, 120, 200, 50, 250])
        elif level == 2:
            base = random.choice([60, 80, 100, 120, 150, 200, 75, 145])
        else:
            base = random.choice([137, 175, 240, 95, 165, 285])
    else:
        if level == 1:
            pct = random.choice([10, 20, 50])
            base = random.choice([40, 60, 80, 100, 120, 200, 50, 250])
        elif level == 2:
            if random.random() < 0.5:
                pct = random.choice([15, 25, 18])
                base = random.choice([60, 80, 100, 120, 200, 150])
            else:
                pct = random.choice([10, 20, 50])
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


GENERATORS = {
    "addition": _gen_addition,
    "subtraction": _gen_subtraction,
    "multiplication": _gen_multiplication,
    "percent_of": _gen_percent_of,
    "money_arithmetic": _gen_money_arithmetic,
}


def generate(
    skill_id: str,
    level: int | None = None,
    mastery: float | None = None,
    target: dict | None = None,
) -> dict:
    fn = GENERATORS.get(skill_id)
    if not fn:
        raise ValueError(f"no generator for skill: {skill_id}")
    if level is None:
        level = mastery_to_level(mastery if mastery is not None else 0.5)
    level = max(1, min(3, int(level)))
    return fn(level, target)
