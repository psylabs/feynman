"""Per-skill problem generators.

Each function takes an explicit difficulty `level` (1=easy, 2=medium, 3=hard).
For drill mode the orchestrator translates current mastery into a level; for
eval mode the level is fixed by the eval plan so problems sample the full
surface area regardless of past performance.

Returns:
    {"prompt": str, "expected": float, "parameters": dict}

The level is also written into `parameters` so per-(skill, level) rollups can
be computed from the attempt log later.
"""

import random


def mastery_to_level(mastery: float) -> int:
    if mastery < 0.4:
        return 1
    if mastery < 0.75:
        return 2
    return 3


def _gen_addition(level: int) -> dict:
    if level == 1:
        a = random.randint(1, 49)
        b = random.randint(1, 49)
        # Bias toward no-carry at L1
        if random.random() < 0.7 and (a % 10) + (b % 10) >= 10:
            b = random.randint(0, 9 - (a % 10)) + (b // 10) * 10
    elif level == 2:
        # Two-digit, often with carry
        a = random.randint(15, 99)
        b = random.randint(15, 99)
    else:
        # Three-digit
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


def _gen_subtraction(level: int) -> dict:
    if level == 1:
        a = random.randint(20, 99)
        b = random.randint(1, a // 2)
        # Bias toward no-borrow
        if (a % 10) < (b % 10) and random.random() < 0.7:
            b = (b // 10) * 10 + random.randint(0, a % 10)
            if b < 1:
                b = 1
    elif level == 2:
        a = random.randint(30, 99)
        b = random.randint(15, a - 1)
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


def _gen_multiplication(level: int) -> dict:
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
        # 2d × 1d
        a = random.randint(13, 49)
        b = random.randint(3, 9)
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "level": level},
    }


def _gen_percent_of(level: int) -> dict:
    if level == 1:
        pct = random.choice([10, 20, 50])
        base = random.choice([40, 60, 80, 100, 120, 200, 50, 250])
    elif level == 2:
        # Either awkward percentage on round base, or round on awkward
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


GENERATORS = {
    "addition": _gen_addition,
    "subtraction": _gen_subtraction,
    "multiplication": _gen_multiplication,
    "percent_of": _gen_percent_of,
}


def generate(skill_id: str, level: int | None = None, mastery: float | None = None) -> dict:
    fn = GENERATORS.get(skill_id)
    if not fn:
        raise ValueError(f"no generator for skill: {skill_id}")
    if level is None:
        level = mastery_to_level(mastery if mastery is not None else 0.5)
    level = max(1, min(3, int(level)))
    return fn(level)
