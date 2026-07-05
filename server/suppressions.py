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

import logging
from pathlib import Path
from typing import Callable

import yaml

MAX_RETRIES = 20
_YAML_PATH = Path(__file__).parent.parent / "suppressions.yaml"
_log = logging.getLogger("feynman.suppressions")

Predicate = Callable[[str, dict], bool]
REGISTRY: dict[str, Predicate] = {}


def rule(name: str) -> Callable[[Predicate], Predicate]:
    def deco(fn: Predicate) -> Predicate:
        REGISTRY[name] = fn
        return fn

    return deco


def _f(params: dict) -> dict:
    return params.get("features") or {}


# Config-form entries: {feature: <name>, <cmp>: <value>}, one comparator
# from lte/gte/eq/in/require. Compiled to a predicate at load time and
# registered under a synthesized name, so matches() needs no changes.
_OPS = {"lte": lambda a, b: a <= b, "gte": lambda a, b: a >= b, "eq": lambda a, b: a == b,
        "in": lambda a, b: a in b, "require": lambda a, b: not a}  # require ignores fv's "b"


def _compile_inline(entry: dict) -> tuple[str, Predicate] | None:
    """Compile a config-form entry, or return ``None`` if malformed.

    Fires when ``feature`` is present in ``params["features"]`` and:
    value<=lte / >=gte / ==eq / in the list / (require, value must be
    ``True``) feature is falsy. Absent feature never fires.
    """
    feature = entry.get("feature")
    cmps = [k for k in _OPS if k in entry]
    if not isinstance(feature, str) or not feature or len(cmps) != 1 or len(entry) != 2:
        return None
    cmp, value = cmps[0], entry[cmps[0]]
    if cmp in ("lte", "gte") and not isinstance(value, (int, float)):
        return None
    if cmp == "in" and not isinstance(value, list):
        return None
    if cmp == "require" and value is not True:
        return None
    op = _OPS[cmp]

    def predicate(skill_id: str, params: dict) -> bool:
        f = _f(params)
        return feature in f and op(f[feature], value)

    return f"inline:{feature}:{cmp}={value!r}", predicate


@rule("by_ten")
def _by_ten(skill_id: str, params: dict) -> bool:
    """True when either operand is a nonzero multiple of 10 (10, 20, ...).

    No exceptions: multiplying/dividing by a power-of-ten factor is a shift,
    not recall practice, regardless of the other operand.
    """
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return (m is not None and m != 0 and m % 10 == 0) or (
        M is not None and M != 0 and M % 10 == 0
    )


@rule("ten_divisor")
def _ten_divisor(skill_id: str, params: dict) -> bool:
    """Dividing by 10/20/... is a decimal shift, not division practice.

    Same doctrine as by_ten (no exceptions for round multiples of 10), but
    keyed on the divisor only — by_ten itself would also fire on the
    dividend (30/5 has max_operand 30) and gut the legit pool.
    """
    b = params.get("b")
    return isinstance(b, (int, float)) and b != 0 and b % 10 == 0


@rule("eleven_times_small")
def _eleven_times_small(skill_id: str, params: dict) -> bool:
    """11 x single-digit is a digit-doubling trick, not recall practice.

    User rule (2026-07-02): for the x11 table, only 11x10 and up count.
    11x11 and 11x12 have min_operand == 11, so they survive.

    Superseded by ``trivial_value`` (2026-07-04), which treats any 11 as
    trivial. Kept registered for skills that don't activate the newer rule.
    """
    f = _f(params)
    m, M = f.get("min_operand"), f.get("max_operand")
    return M == 11 and m is not None and m < 10


# User rule (2026-07-04): a fact is too simple when ANY value in it — either
# operand or the result — is a small number or 10/11 (skip counting, decimal
# shifts, digit-doubling). Small means <= 5.
_TRIVIAL_EXTRA = frozenset({10, 11})


@rule("trivial_value")
def _trivial_value(skill_id: str, params: dict) -> bool:
    a, b = params.get("a"), params.get("b")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or not b:
        return False
    result = a / b if skill_id == "division" else a * b
    return any(v <= 5 or v in _TRIVIAL_EXTRA for v in (a, b, result))


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
    for sid, entries in data.items():
        if not isinstance(entries, list):
            continue
        names: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                if entry in REGISTRY:
                    names.append(entry)
            elif isinstance(entry, dict) and (compiled := _compile_inline(entry)):
                name, fn = compiled
                REGISTRY[name] = fn
                names.append(name)
            else:
                _log.warning("suppressions.yaml: malformed rule for %s: %r", sid, entry)
        cleaned[sid] = names
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
