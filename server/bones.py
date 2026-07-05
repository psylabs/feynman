"""Shared predicate + feature stamper — the bottom of the stack.

Doctrine (bones-doctrine, 2026-07-05):

- **Features describe.** A feature is a measurable fact about a problem's
  bones (operands, op, result, crosses_ten, has_borrow...), stamped by
  whoever builds the problem — sampler, skin, or forge. Features never
  decide.
- **Rules judge.** A rule is declarative policy over features (see
  ``suppressions.py``). Rules are the single source of truth for validity.
- **Generators construct and pre-satisfy.** Samplers check candidates
  against the active rule set inside their rejection loops.
- **Crossing semantics.** A problem is valid only if a multiple of 10 lies
  strictly between the ENDPOINTS of the number-line segment the mental
  computation traverses, after stripping the endpoints' common trailing
  zeros (190-90 is judged as 19-9 -> invalid; 1039+59 stays hard -> valid).
  Movement ops (a +/- b) use endpoints (a, result); difference ops ("how
  much more is x than y") use endpoints (x, y). Landing exactly on a ten
  does not count.

This module imports no other server module — it sits below everything.
Nothing in it depends on the generator, suppressions, or any skin; they all
depend on this instead.
"""

from __future__ import annotations


def crosses_ten(x: int, y: int) -> bool:
    """True when a multiple of 10 lies strictly between endpoints x and y,
    judged after stripping the endpoints' common trailing zeros.

    Landing exactly on a ten does not count: the ``hi - 1`` demotes a
    result sitting exactly on a ten into the lower decade band, so it reads
    as no crossing. Equal endpoints never cross, even when the shared value
    is itself a multiple of ten.

    Negative endpoints register a crossing at zero (0 % 10 == 0, so a zero
    endpoint just defers reduction to the other endpoint's trailing zeros).
    Upstream generators clamp operands non-negative, so this stays moot —
    documented here, not handled.
    """
    if x == y:
        return False
    while x % 10 == 0 and y % 10 == 0:  # 0 % 10 == 0, so 0 defers to the other endpoint
        x //= 10
        y //= 10
    lo, hi = sorted((x, y))
    return (hi - 1) // 10 != lo // 10  # hi-1 demotes landing-exactly-on-a-ten


def compute_features(op: str, operands, *, endpoints=None, extra=None) -> dict:
    """Stamp a feature dict describing a problem's bones.

    ``op`` is one of ``"+"``, ``"-"``, ``"*"``, ``"/"``, ``"pct"``.
    ``operands`` holds 2+ numbers for ``"+"``, exactly 2 for everything
    else. For ``"/"`` the caller passes ``(divisor, quotient)`` — the two
    recalled facts — not ``(dividend, divisor)``. For ``"pct"`` the caller
    passes ``(percent, base)``.

    Always present: ``min_operand``, ``max_operand`` over ``operands``;
    ``abs_diff`` when ``operands`` has exactly 2 elements.

    ``"+"``: ``has_carry`` is True when any running-sum step has
    ``(run % 10) + (x % 10) >= 10``. ``crosses_ten`` is
    ``crosses_ten(*endpoints)`` when ``endpoints`` is given, else requires
    EVERY running partial sum to cross: ``crosses_ten(run, run + x)`` for
    each step.

    ``"-"``: ``operands`` is ``(a, b)``. ``has_borrow`` is
    ``(a % 10) < (b % 10)``. ``crosses_ten`` is ``crosses_ten(*endpoints)``
    when given (difference ops pass the two compared values), else
    ``crosses_ten(a, a - b)`` (movement default).

    ``"*"``, ``"/"``, ``"pct"``: no carry/borrow/crossing keys.

    ``extra`` is a dict merged last, so its keys win over computed ones.
    """
    operands = list(operands)
    features: dict = {
        "min_operand": min(operands),
        "max_operand": max(operands),
    }
    if len(operands) == 2:
        features["abs_diff"] = abs(operands[0] - operands[1])

    if op == "+":
        run = operands[0]
        has_carry = False
        every_step_crosses = True
        for x in operands[1:]:
            if (run % 10) + (x % 10) >= 10:
                has_carry = True
            if not crosses_ten(run, run + x):
                every_step_crosses = False
            run += x
        features["has_carry"] = has_carry
        features["crosses_ten"] = (
            crosses_ten(*endpoints) if endpoints is not None else every_step_crosses
        )
    elif op == "-":
        a, b = operands
        features["has_borrow"] = (a % 10) < (b % 10)
        features["crosses_ten"] = (
            crosses_ten(*endpoints) if endpoints is not None else crosses_ten(a, a - b)
        )
    # "*", "/", "pct": no carry/borrow/crossing keys.

    if extra:
        features.update(extra)
    return features
