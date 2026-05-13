"""Predicate-based suppression of trivial problems.

A *rule* is a ``(skill_id, parameters) -> bool`` predicate that returns
``True`` when a sampled problem is too trivial to drill. The generator
re-samples up to ``MAX_RETRIES`` times before giving up and emitting the
last candidate anyway.

Rules are registered with the ``@rule(name)`` decorator. Activation is
data: ``suppressions.yaml`` at the repo root maps ``skill_id`` to a list
of rule names. Adding a new *kind* of rule is a small Python change here;
turning rules on or off is a YAML edit.

When ``generate()`` is called with a pinned ``target`` (e.g. the
scheduler asked for a specific fact), suppressions are bypassed — the
caller deliberately asked for that problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

MAX_RETRIES = 20
_YAML_PATH = Path(__file__).parent.parent / "suppressions.yaml"

Predicate = Callable[[str, dict], bool]
REGISTRY: dict[str, Predicate] = {}

# Differences the user has explicitly opted out of for subtraction. Edit in
# suppressions.yaml? No — these are conceptually part of the rule itself.
# Edit here when the set of "trivial round differences" changes.
_ROUND_DIFFS = frozenset({5, 10, 50, 100, 200, 300, 400, 500, 1000})


def rule(name: str) -> Callable[[Predicate], Predicate]:
    def deco(fn: Predicate) -> Predicate:
        REGISTRY[name] = fn
        return fn

    return deco


def _ab(params: dict) -> tuple[int | None, int | None]:
    a, b = params.get("a"), params.get("b")
    if a is None or b is None:
        return None, None
    return int(a), int(b)


@rule("single_digit_small")
def _single_digit_small(skill_id: str, params: dict) -> bool:
    """Both operands in [0, 2]. Trivial for an adult."""
    a, b = _ab(params)
    if a is None:
        return False
    return abs(a) <= 2 and abs(b) <= 2


@rule("trivial_diff")
def _trivial_diff(skill_id: str, params: dict) -> bool:
    """Subtraction whose difference is <= 2 (e.g. 9567 - 9566)."""
    if skill_id != "subtraction":
        return False
    a, b = _ab(params)
    if a is None:
        return False
    return abs(a - b) <= 2


@rule("round_diff")
def _round_diff(skill_id: str, params: dict) -> bool:
    """Subtraction whose difference is a canonical round number."""
    if skill_id != "subtraction":
        return False
    a, b = _ab(params)
    if a is None:
        return False
    return abs(a - b) in _ROUND_DIFFS


@rule("subtract_zero")
def _subtract_zero(skill_id: str, params: dict) -> bool:
    if skill_id != "subtraction":
        return False
    return params.get("b") == 0


@rule("by_ten")
def _by_ten(skill_id: str, params: dict) -> bool:
    """Multiplication where one factor is 10."""
    if skill_id != "multiplication":
        return False
    a, b = _ab(params)
    if a is None:
        return False
    return a == 10 or b == 10


@rule("equal_operands")
def _equal_operands(skill_id: str, params: dict) -> bool:
    a, b = _ab(params)
    if a is None:
        return False
    return a == b


# ---- loader ---------------------------------------------------------------

_active_cache: dict[str, list[str]] | None = None


def load_active(force: bool = False) -> dict[str, list[str]]:
    """Read ``suppressions.yaml`` into ``{skill_id: [rule_name, ...]}``.

    Cached after the first call; pass ``force=True`` to re-read from disk.
    """
    global _active_cache
    if _active_cache is not None and not force:
        return _active_cache
    if not _YAML_PATH.exists():
        _active_cache = {}
        return _active_cache
    with _YAML_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    cleaned: dict[str, list[str]] = {}
    for sid, names in data.items():
        if not isinstance(names, list):
            continue
        cleaned[sid] = [n for n in names if n in REGISTRY]
    _active_cache = cleaned
    return cleaned


def matches(
    skill_id: str,
    params: dict,
    active: dict[str, list[str]] | None = None,
) -> str | None:
    """Return the first matching rule's name, or ``None``."""
    if active is None:
        active = load_active()
    names = active.get(skill_id, [])
    for name in names:
        fn = REGISTRY.get(name)
        if fn and fn(skill_id, params):
            return name
    return None
