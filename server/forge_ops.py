"""Closed compute registry for the template forge.

The LLM can only pick ops from this dict; every accepted pool entry has an
`op` key that maps to one of these callables.  The forge validator calls
``OPS[op](*args)`` to confirm the computation runs without error.

Importable by both ``tools/template_forge.py`` and the generators (money.py,
weather.py, tools/template_forge.py) via ``features_for_entry``.
"""
from __future__ import annotations

from typing import Callable

from server import bones

OPS: dict[str, Callable[..., float]] = {
    "sub":      lambda a, b: a - b,
    "add_list": lambda *xs: sum(xs),
    "pct_of":   lambda pct, base: pct * base / 100,
    "div":      lambda a, b: a / b,
    "delta":    lambda a, b: abs(a - b),
}


def features_for_entry(entry: dict) -> dict | None:
    """Map a forge pool entry to a ``bones`` feature dict, or ``None``.

    Bones doctrine (2026-07-05): forge entries are first-class citizens of
    the suppression pipeline — a served pool entry must carry the same
    features a generator-sampled problem would, so the active rule set can
    judge it. Mapping per ``op``:

    - ``sub``: "-" movement, ``operands=(a, b)`` (no explicit endpoints —
      movement default, i.e. ``crosses_ten(a, a - b)``).
    - ``delta``: "-" difference, ``endpoints=(args[0], args[1])`` (the two
      recalled values, not a movement).
    - ``add_list``: "+" over all args.
    - ``div``: "/" with operands ``(divisor, quotient)`` = ``(b, a / b)`` —
      the two recalled facts, matching ``bones.compute_features``'s
      division convention.
    - ``pct_of``: "pct" over ``(percent, base)``.

    ``extra={"operation": entry["operation"]}`` is merged into every result.

    Returns ``None`` for an unknown ``op``, a malformed/wrong-arity ``args``,
    or any arithmetic failure (e.g. division by zero). Also returns ``None``
    when any operand isn't integral (``float.is_integer()``) — bones is
    place-value logic over integers; fractional operands have no crossing
    semantics, so coercing them to int would be lossy and wrong.
    """
    op = entry.get("op")
    args = entry.get("args")
    if op not in OPS or not isinstance(args, (list, tuple)) or not args:
        return None
    try:
        ints: list[int] = []
        for a in args:
            f = float(a)
            if not f.is_integer():
                return None
            ints.append(int(f))

        extra = {"operation": entry.get("operation")}
        if op == "sub":
            a, b = ints
            return bones.compute_features("-", (a, b), extra=extra)
        if op == "delta":
            a, b = ints
            return bones.compute_features("-", (a, b), endpoints=(a, b), extra=extra)
        if op == "add_list":
            return bones.compute_features("+", ints, extra=extra)
        if op == "div":
            a, b = ints
            quotient = a / b
            if not float(quotient).is_integer():
                return None
            return bones.compute_features("/", (b, int(quotient)), extra=extra)
        if op == "pct_of":
            pct, base = ints
            return bones.compute_features("pct", (pct, base), extra=extra)
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    return None
