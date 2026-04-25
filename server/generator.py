"""Per-skill problem generators.

Each function takes the user's current mastery (0..1) and returns:
    {"prompt": str, "expected": float, "parameters": dict}

Parameter ranges scale with mastery — easier numbers when low, harder as it climbs.
"""

import random


def _scale_max(mastery: float, lo: int, hi: int) -> int:
    """Cap the upper end of a range based on mastery."""
    span = hi - lo
    return lo + max(1, int(round(span * (0.3 + 0.7 * mastery))))


def _gen_addition(mastery: float) -> dict:
    cap = _scale_max(mastery, 5, 99)
    a = random.randint(1, cap)
    b = random.randint(1, cap)
    return {
        "prompt": f"What is {a} plus {b}?",
        "expected": float(a + b),
        "parameters": {
            "a": a,
            "b": b,
            "carry": ((a % 10) + (b % 10)) >= 10,
            "cap": cap,
        },
    }


def _gen_subtraction(mastery: float) -> dict:
    cap = _scale_max(mastery, 15, 99)
    a = random.randint(10, cap)
    b = random.randint(1, a)
    return {
        "prompt": f"What is {a} minus {b}?",
        "expected": float(a - b),
        "parameters": {
            "a": a,
            "b": b,
            "borrow": (a % 10) < (b % 10),
            "cap": cap,
        },
    }


def _gen_multiplication(mastery: float) -> dict:
    cap = _scale_max(mastery, 5, 12)
    a = random.randint(2, cap)
    b = random.randint(2, 12)
    return {
        "prompt": f"What is {a} times {b}?",
        "expected": float(a * b),
        "parameters": {"a": a, "b": b, "cap": cap},
    }


def _gen_percent_of(mastery: float) -> dict:
    if mastery < 0.4:
        pcts = [10, 20, 50]
        bases = [50, 100, 200, 80, 60]
    elif mastery < 0.7:
        pcts = [10, 15, 20, 25, 50]
        bases = [40, 60, 80, 100, 120, 150, 200]
    else:
        pcts = [5, 10, 15, 18, 20, 25, 50]
        bases = [37, 64, 80, 120, 140, 175, 240, 65, 90]

    pct = random.choice(pcts)
    base = random.choice(bases)
    expected = pct * base / 100.0
    return {
        "prompt": f"What is {pct} percent of {base}?",
        "expected": expected,
        "parameters": {"percentage": pct, "base": base},
    }


def _gen_time_elapsed(mastery: float) -> dict:
    if mastery < 0.5:
        # within an hour
        start_h = random.randint(1, 11)
        start_m = random.randint(0, 30)
        duration = random.randint(5, 59 - start_m)
    else:
        # span hours
        start_h = random.randint(1, 11)
        start_m = random.randint(0, 59)
        duration = random.randint(20, 120)

    end_total = start_h * 60 + start_m + duration
    end_h_raw = end_total // 60
    end_m = end_total % 60
    end_h = end_h_raw if end_h_raw <= 12 else end_h_raw - 12

    return {
        "prompt": (
            f"If you start at {start_h}:{start_m:02d} "
            f"and finish at {end_h}:{end_m:02d}, how many minutes elapsed?"
        ),
        "expected": float(duration),
        "parameters": {
            "start": f"{start_h}:{start_m:02d}",
            "end": f"{end_h}:{end_m:02d}",
            "duration_min": duration,
            "spans_hour": (start_m + duration) >= 60,
        },
    }


GENERATORS = {
    "addition": _gen_addition,
    "subtraction": _gen_subtraction,
    "multiplication": _gen_multiplication,
    "percent_of": _gen_percent_of,
    "time_elapsed": _gen_time_elapsed,
}


def generate(skill_id: str, mastery: float) -> dict:
    fn = GENERATORS.get(skill_id)
    if not fn:
        raise ValueError(f"no generator for skill: {skill_id}")
    return fn(mastery)
