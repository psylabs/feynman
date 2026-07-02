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
scheduler asked for a specific fact), the first candidate is still checked.
If it matches an active rule, the generator drops the target hint and
re-samples freely. Active suppressions win over scheduler hints.
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


def _f(params: dict) -> dict:
    return params.get("features") or {}


@rule("single_digit_small")
def _single_digit_small(skill_id: str, params: dict) -> bool:
    return _f(params).get("max_operand", float("inf")) <= 2


@rule("trivial_diff")
def _trivial_diff(skill_id: str, params: dict) -> bool:
    v = _f(params).get("abs_diff")
    return v is not None and v <= 2


@rule("round_diff")
def _round_diff(skill_id: str, params: dict) -> bool:
    v = _f(params).get("abs_diff")
    return v is not None and v in _ROUND_DIFFS


@rule("subtract_zero")
def _subtract_zero(skill_id: str, params: dict) -> bool:
    return _f(params).get("min_operand") == 0


@rule("by_ten")
def _by_ten(skill_id: str, params: dict) -> bool:
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return m is not None and (m == 10 or M == 10)


@rule("equal_operands")
def _equal_operands(skill_id: str, params: dict) -> bool:
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return m is not None and m == M


@rule("small_operand")
def _small_operand(skill_id: str, params: dict) -> bool:
    return _f(params).get("min_operand", float("inf")) <= 2


# ---- loader ---------------------------------------------------------------

_active_cache: dict[str, list[str]] | None = None
_cache_mtime: float | None = None


def load_active(force: bool = False) -> dict[str, list[str]]:
    """Read ``suppressions.yaml`` into ``{skill_id: [rule_name, ...]}``.

    Cached; automatically re-reads when the file's mtime changes so rule
    edits take effect without restarting the server. Pass ``force=True`` to
    unconditionally re-read from disk.
    """
    global _active_cache, _cache_mtime
    if not _YAML_PATH.exists():
        _active_cache = {}
        _cache_mtime = None
        return _active_cache
    current_mtime = _YAML_PATH.stat().st_mtime
    if _active_cache is not None and not force and current_mtime == _cache_mtime:
        return _active_cache
    with _YAML_PATH.open() as f:
        data = yaml.safe_load(f) or {}
    cleaned: dict[str, list[str]] = {}
    for sid, names in data.items():
        if not isinstance(names, list):
            continue
        cleaned[sid] = [n for n in names if n in REGISTRY]
    _active_cache = cleaned
    _cache_mtime = current_mtime
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
