"""Taxonomy v2: classify attempts into primitive facts vs compound procedures.

Primitives are finite recall items (judged on onset latency — time to start
speaking). Compounds are procedures (judged on resolution latency vs the
skill's target). Pure functions, no I/O. This is fact_key_v2; the legacy
diagnosis.fact_key stays untouched until the scheduler swaps over.

Unlike the generator's boolean carry/borrow flags, classification here works
column-by-column from the operands, so "565 - 85" is a tens-borrow problem
(family sub.3d-2d.bt) even though its ones column never borrows.

Prototyped in tools/taxonomy_v2.py against the full attempt history.
"""
from dataclasses import dataclass, field

PRIMITIVE_ONSET_TARGET_MS = 1200
MULT_TABLE = frozenset({12, 13, 14, 15, 16, 17, 18, 19})
_COL = "oth"  # ones, tens, hundreds (column letters for family suffixes)
_COL_WORDS = {"o": "ones", "t": "tens", "h": "hundreds"}


@dataclass(frozen=True)
class Taxon:
    tier: str      # "primitive" | "compound"
    family: str    # assessment/level grain, e.g. "sub.3d-2d.bt"
    key: str       # schedulable item (FSRS unit), e.g. "sub:18-4"
    features: dict = field(default_factory=dict)


def carry_columns(a: int, b: int) -> list[int]:
    """Columns (0=ones) where computing a+b column-wise produces a carry."""
    cols, carry, i = [], 0, 0
    while a or b or carry:
        s = a % 10 + b % 10 + carry
        carry = 1 if s >= 10 else 0
        if carry:
            cols.append(i)
        a //= 10
        b //= 10
        i += 1
    return cols


def borrow_columns(a: int, b: int) -> list[int]:
    """Columns (0=ones) where computing a-b column-wise needs a borrow. a >= b >= 0."""
    cols, borrow, i = [], 0, 0
    while b or borrow:
        d = a % 10 - b % 10 - borrow
        borrow = 1 if d < 0 else 0
        if borrow:
            cols.append(i)
        a //= 10
        b //= 10
        i += 1
    return cols


def _digits(n) -> int:
    n = abs(int(n))
    return 1 if n < 10 else 2 if n < 100 else 3 if n < 1000 else 4


def _suffix(prefix: str, cols: list[int]) -> str:
    if not cols:
        return "n"
    return prefix + "".join(_COL[c] for c in cols if c < len(_COL))


def _across_zero(a: int, cols: list[int]) -> bool:
    """True when any borrow pulls from a zero digit of the minuend."""
    return any((a // 10 ** (c + 1)) % 10 == 0 for c in cols)


def classify(skill_id: str, parameters: dict) -> Taxon | None:
    """Extract the fact_key_v2 taxon from attempt parameters, or None."""
    if not parameters or not isinstance(parameters, dict):
        return None
    a, b = parameters.get("a"), parameters.get("b")

    if skill_id == "addition" and a is not None and b is not None:
        a, b = int(a), int(b)
        lo, hi = sorted([a, b])
        cols = carry_columns(a, b)
        if a + b <= 20:
            fam = "add.bridge10" if cols else "add.within20"
            return Taxon("primitive", fam, f"add:{lo}+{hi}")
        fam = f"add.{_digits(lo)}d+{_digits(hi)}d.{_suffix('c', cols)}"
        feats = {"carry_cols": cols,
                 "crosses_hundred": (a < 100 and b < 100 and a + b >= 100)}
        return Taxon("compound", fam, fam, feats)

    if skill_id == "subtraction" and a is not None and b is not None:
        a, b = int(a), int(b)
        cols = borrow_columns(a, b)
        if a <= 20:
            fam = "sub.borrow20" if cols else "sub.within20"
            return Taxon("primitive", fam, f"sub:{a}-{b}")
        fam = f"sub.{_digits(a)}d-{_digits(b)}d.{_suffix('b', cols)}"
        feats = {"borrow_cols": cols, "across_zero": _across_zero(a, cols),
                 "crosses_hundred": (a >= 100) != (a - b >= 100)}
        return Taxon("compound", fam, fam, feats)

    if skill_id == "multiplication" and a is not None and b is not None:
        lo, hi = sorted([int(a), int(b)])
        if hi in MULT_TABLE:
            return Taxon("primitive", f"mul.x{hi}", f"mul:{lo}x{hi}")
        fam = f"mul.{_digits(hi)}dx{_digits(lo)}d"
        return Taxon("compound", fam, fam)

    if skill_id == "division" and a is not None and b is not None:
        a, b = int(a), int(b)
        if b and a % b == 0:
            q = a // b
            lo, hi = sorted([b, q])
            if hi in MULT_TABLE:
                return Taxon("primitive", f"div.x{hi}", f"div:{lo}x{hi}")
        return Taxon("compound", "div.multi", "div.multi")

    if skill_id == "percent_of":
        pct = parameters.get("percentage")
        if pct is None:
            return None
        return Taxon("compound", "pct", f"pct:{int(pct)}")

    if skill_id in ("money_arithmetic", "weather_math"):
        op = parameters.get("operation")
        if not op:
            return None
        prefix = "money" if skill_id == "money_arithmetic" else "weather"
        return Taxon("compound", f"{prefix}.{op}", f"{prefix}:{op}")

    return None


_FIXED_DISPLAY = {
    "add.within20": "addition within 20",
    "add.bridge10": "addition within 20 (bridging 10)",
    "sub.within20": "subtraction within 20",
    "sub.borrow20": "subtraction within 20 (borrow)",
    "div.multi": "multi-digit division",
    "pct": "percent of a number",
}


def family_display(family: str) -> str:
    """Human label for a family id: 'sub.3d-2d.bt' -> '3-digit − 2-digit, borrow in tens'."""
    if family in _FIXED_DISPLAY:
        return _FIXED_DISPLAY[family]
    if family.startswith("mul.x"):
        return f"×{family.removeprefix('mul.x')} table"
    if family.startswith("div.x"):
        return f"÷{family.removeprefix('div.x')} family"
    parts = family.split(".")
    if len(parts) == 3 and parts[0] in ("add", "sub"):
        op = "+" if parts[0] == "add" else "−"
        sep = "+" if "+" in parts[1] else "-"
        lo, hi = parts[1].split(sep)
        shape = f"{lo.rstrip('d')}-digit {op} {hi.rstrip('d')}-digit"
        suf = parts[2]
        if suf == "n":
            detail = "no carry" if parts[0] == "add" else "no borrow"
        else:
            word = "carry" if suf[0] == "c" else "borrow"
            places = " and ".join(_COL_WORDS[ch] for ch in suf[1:])
            detail = f"{word} in {places}"
        return f"{shape}, {detail}"
    if parts[0] in ("money", "weather"):
        return family.replace(".", ": ").replace("_", " ")
    return family
