"""Deterministic correctness check. Never goes through an LLM."""


def grade(parsed: float | None, expected: float, tolerance: dict) -> dict:
    if parsed is None:
        return {"correct": False, "error_magnitude": None, "rule": "no_answer"}

    err = parsed - expected
    abs_err = abs(err)
    t_type = tolerance.get("type", "exact")

    if t_type == "exact":
        return {
            "correct": parsed == expected,
            "error_magnitude": err,
            "rule": "exact",
        }

    if t_type == "absolute":
        bound = float(tolerance["value"])
        return {
            "correct": abs_err <= bound,
            "error_magnitude": err,
            "rule": f"abs<={bound}",
        }

    if t_type == "relative":
        frac = float(tolerance["fraction"])
        bound = max(float(tolerance.get("min_abs", 0)), abs(expected) * frac)
        return {
            "correct": abs_err <= bound,
            "error_magnitude": err,
            "rule": f"rel<={frac}",
        }

    if t_type == "money":
        # the larger of $1 or 1% of expected
        bound = max(1.0, abs(expected) * 0.01)
        return {
            "correct": abs_err <= bound,
            "error_magnitude": err,
            "rule": f"money(<=${bound:.2f})",
        }

    # default to exact
    return {
        "correct": parsed == expected,
        "error_magnitude": err,
        "rule": "default_exact",
    }
