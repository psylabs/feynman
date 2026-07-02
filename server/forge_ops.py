"""Closed compute registry for the template forge.

The LLM can only pick ops from this dict; every accepted pool entry has an
`op` key that maps to one of these callables.  The forge validator calls
``OPS[op](*args)`` to confirm the computation runs without error.

Importable by both ``tools/template_forge.py`` and (next task) the generators.
"""
from __future__ import annotations

from typing import Callable

OPS: dict[str, Callable[..., float]] = {
    "sub":      lambda a, b: a - b,
    "add_list": lambda *xs: sum(xs),
    "pct_of":   lambda pct, base: pct * base / 100,
    "div":      lambda a, b: a / b,
    "delta":    lambda a, b: abs(a - b),
}
