"""Verified mental-math strategy library.

Every tip teaches a shortcut, and every tip must be arithmetically true. Each
``build`` recomputes its own decomposition and ``assert``s it reproduces the
graded ``expected`` answer (exactly for exact-tolerance skills, within the
money tolerance for finance estimates). ``tips_for`` runs each build inside a
try/except so a decomposition that can't verify is dropped, never shown — the
assert is the contract, the except is the safety net.

Families are matched by PREFIX against the taxon family from
``server.taxonomy.classify`` (e.g. a strategy listing ``"sub.3d"`` applies to
``sub.3d-2d.bt``). At most two tips are returned per attempt.

The 14 add/sub/mul/money/weather strategies are ported verbatim from the
verified ``tools/taxonomy_v2.py`` prototype; 12 more (complement_100,
doubles_anchor, times_4/5/8/11/15/20, div_as_inverse, pct_anchor,
sub_same_ones, f_between) are added with the same assert-verified shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from server.taxonomy import classify


@dataclass(frozen=True)
class Strategy:
    name: str
    families: tuple[str, ...]                      # family PREFIXES this applies to
    build: Callable[[dict, float], "str | None"]   # (parameters, expected) -> tip | None


def _fmt(x: float) -> str:
    return f"{x:g}" if x != int(x) else str(int(x))


def _money_ok(est, expected) -> bool:
    """The actual grading tolerance for money skills: max($1, 1% of expected)."""
    return abs(est - expected) <= max(1.0, 0.01 * abs(expected))


# ----------------------------------------------------------- subtraction

def strat_count_up(p, expected):
    """Small-difference subtraction: count up from b to a instead of taking away."""
    a, b = int(p["a"]), int(p["b"])
    if not (0 < a - b <= 4):
        return None
    assert a - b == expected
    return (f"{a}-{b}: the gap is small — count UP from {b}. "
            f"{b}→{a} is {a - b}. Never 'take away' when operands are this close.")


def strat_subtract_in_hops(p, expected):
    """Borrow subtraction: overshoot to the round number, add back."""
    a, b = int(p["a"]), int(p["b"])
    if b < 5 or not p.get("borrow", False):
        return None
    up = ((b + 9) // 10) * 10
    back = up - b
    assert a - up + back == expected
    return (f"{a}-{b}: don't borrow — subtract {up} then add back {back}. "
            f"{a}-{up}={a - up}, +{back}={a - b}.")


def strat_left_to_right_sub(p, expected):
    """Multi-digit no-borrow subtraction: tens first, then ones."""
    a, b = int(p["a"]), int(p["b"])
    if a < 21 or p.get("borrow", False):
        return None
    tens, ones = (b // 10) * 10, b % 10
    assert a - tens - ones == expected
    if ones == 0:
        return (f"{a}-{b}: ignore the trailing zero — {a // 10}-{b // 10}="
                f"{a // 10 - b // 10}, so {a - b}.")
    return (f"{a}-{b}: left to right. {a}-{tens}={a - tens}, then -{ones}={a - b}.")


def strat_complement_100(p, expected):
    """Subtracting from a power of ten: use the all-nines complement, then +1."""
    a, b = int(p["a"]), int(p["b"])
    if a not in (100, 1000) or not (0 < b < a):
        return None
    nines = a - 1                 # 99 or 999: subtracting needs no borrow
    partial = nines - b
    assert partial + 1 == expected
    return (f"{a}-{b}: no borrowing — take {b} from {nines} first "
            f"({nines}-{b}={partial}), then add the 1 back → {partial + 1}.")


def strat_sub_same_ones(p, expected):
    """Ones digits match: they cancel, leaving a pure tens subtraction."""
    a, b = int(p["a"]), int(p["b"])
    if a < b or a % 10 != b % 10:
        return None
    assert (a // 10 - b // 10) * 10 == expected
    return (f"{a}-{b}: the ones match, so they cancel — just {a // 10}-{b // 10}="
            f"{a // 10 - b // 10} tens → {a - b}.")


# ----------------------------------------------------------- addition

def strat_bridge_ten(p, expected):
    """Single-digit addition crossing 10: fill to 10, add the rest."""
    a, b = sorted([int(p["a"]), int(p["b"])], reverse=True)
    if not (a < 10 and a + b > 10 and b > 1):
        return None
    fill = 10 - a
    rest = b - fill
    assert 10 + rest == expected
    return (f"{a}+{b}: fill to ten. {a}+{fill}=10, then +{rest}={10 + rest}.")


def strat_doubles_anchor(p, expected):
    """Near-doubles addition: double the smaller, nudge by the difference."""
    a, b = int(p["a"]), int(p["b"])
    if abs(a - b) > 2:
        return None
    lo, diff = min(a, b), abs(a - b)
    assert 2 * lo + diff == expected
    return (f"{a}+{b}: near-doubles. Double {lo} is {2 * lo}, +{diff} "
            f"→ {2 * lo + diff}.")


def strat_compensation_add(p, expected):
    """Addition with an operand near a round number: round up, pull back."""
    a, b = int(p["a"]), int(p["b"])
    for x, y in ((a, b), (b, a)):
        up = ((x + 9) // 10) * 10
        over = up - x
        if 0 < over <= 2 and x > 10:
            assert y + up - over == expected
            return (f"{x}+{y}: treat {x} as {up}. {y}+{up}={y + up}, "
                    f"take back {over} → {y + up - over}.")
    return None


def strat_add_tens_then_ones(p, expected):
    """Multi-digit addition: add tens, then ones."""
    a, b = sorted([int(p["a"]), int(p["b"])], reverse=True)
    if b < 10:
        return None
    tens, ones = (b // 10) * 10, b % 10
    assert a + tens + ones == expected
    return (f"{a}+{b}: {a}+{tens}={a + tens}, then +{ones}={a + b}.")


# ----------------------------------------------------------- multiplication

def strat_times_9(p, expected):
    a, b = int(p["a"]), int(p["b"])
    if 9 not in (a, b):
        return None
    other = a if b == 9 else b
    assert other * 10 - other == expected
    return (f"{a}×{b}: ×9 is ×10 minus one of them. {other}×10={other * 10}, "
            f"-{other}={other * 10 - other}.")


def strat_times_12(p, expected):
    a, b = int(p["a"]), int(p["b"])
    if 12 not in (a, b):
        return None
    other = a if b == 12 else b
    assert other * 10 + other * 2 == expected
    return (f"{a}×{b}: ×12 = ×10 + ×2. {other}0 + {other * 2} = {other * 12}.")


def strat_times_11(p, expected):
    """×11: for a two-digit n, drop the digit-sum between its digits."""
    a, b = int(p["a"]), int(p["b"])
    if 11 not in (a, b):
        return None
    n = a if b == 11 else b
    hi, lo = n // 10, n % 10
    if n >= 10 and hi + lo < 10:
        assert hi * 100 + (hi + lo) * 10 + lo == expected
        return (f"{a}×{b}: split the digits of {n} — {hi} and {lo}, drop their "
                f"sum {hi + lo} between them → {hi * 100 + (hi + lo) * 10 + lo}.")
    assert 11 * n == expected
    return f"{a}×{b}: ×11 = ×10 + itself. {n}0+{n}={11 * n}."


def strat_times_4(p, expected):
    """×4 = double twice."""
    a, b = int(p["a"]), int(p["b"])
    if 4 not in (a, b):
        return None
    other = b if a == 4 else a
    assert other * 4 == expected
    return f"{a}×{b}: ×4 = double twice. {other}→{other * 2}→{other * 4}."


def strat_times_5(p, expected):
    """×5 = ×10 then halve."""
    a, b = int(p["a"]), int(p["b"])
    if 5 not in (a, b):
        return None
    other = b if a == 5 else a
    assert other * 5 == expected
    return (f"{a}×{b}: ×5 = ×10 then halve. {other}×10={other * 10}, "
            f"/2 → {other * 10 // 2}.")


def strat_times_8(p, expected):
    """×8 = double three times."""
    a, b = int(p["a"]), int(p["b"])
    if 8 not in (a, b):
        return None
    other = b if a == 8 else a
    assert other * 8 == expected
    return (f"{a}×{b}: ×8 = double three times. "
            f"{other}→{other * 2}→{other * 4}→{other * 8}.")


def strat_times_15(p, expected):
    """×15 = ×10 plus half again."""
    a, b = int(p["a"]), int(p["b"])
    if 15 not in (a, b):
        return None
    other = b if a == 15 else a
    ten, half = other * 10, other * 10 // 2
    assert ten + half == expected
    return f"{a}×{b}: ×15 = ×10 + half again. {other}×10={ten}, +{half} → {ten + half}."


def strat_times_20(p, expected):
    """×20 = double then ×10."""
    a, b = int(p["a"]), int(p["b"])
    if 20 not in (a, b):
        return None
    other = b if a == 20 else a
    assert other * 20 == expected
    return (f"{a}×{b}: ×20 = double then ×10. {other}×2={other * 2} "
            f"→ {other * 2 * 10}.")


def strat_near_square(p, expected):
    """(n-d)(n+d) = n² - d² for operands straddling a round anchor."""
    a, b = sorted([int(p["a"]), int(p["b"])])
    if (a + b) % 2:
        return None
    n, d = (a + b) // 2, (b - a) // 2
    if d == 0 or d > 3 or n % 5:
        return None
    assert n * n - d * d == expected
    return (f"{a}×{b}: straddles {n}. {n}²-{d}²={n * n}-{d * d}={n * n - d * d}.")


# ----------------------------------------------------------- division

def strat_div_as_inverse(p, expected):
    """Division as a missing-factor: what times the divisor makes the dividend?"""
    a, b = int(p["a"]), int(p["b"])
    if b == 0 or a % b != 0:
        return None
    q = a // b
    assert b * q == a and q == expected
    return f"{a}÷{b}: flip it — what times {b} makes {a}? {q}."


# ----------------------------------------------------------- percent / money

def strat_pct_anchor(p, expected):
    """Friendly percentages via the 10%/half/quarter anchors.

    Handles ``percent_of`` ({percentage, base}), restaurant tips
    ({tip_percent, bill_amount}) and category slices ({percentage, total}) —
    all graded on the money tolerance.
    """
    pct = p.get("percentage")
    if pct is None:
        pct = p.get("tip_percent")
    if pct is None:
        return None
    pct = int(pct)
    base = p.get("base")
    if base is None:
        base = p.get("bill_amount")
    if base is None:
        base = p.get("total")
    if base is None:
        return None
    base = float(base)

    if pct == 10:
        est, how = base / 10, f"shift the decimal → {_fmt(base / 10)}"
    elif pct == 5:
        est, how = base / 20, f"half of 10% ({_fmt(base / 10)}) → {_fmt(base / 20)}"
    elif pct == 15:
        est, how = base * 0.15, (f"10% is {_fmt(base / 10)}, plus half again "
                                 f"{_fmt(base / 20)} → {_fmt(base * 0.15)}")
    elif pct == 20:
        est, how = base * 0.20, f"10% is {_fmt(base / 10)}, double it → {_fmt(base / 5)}"
    elif pct == 25:
        est, how = base / 4, f"quarter it → {_fmt(base / 4)}"
    elif pct == 50:
        est, how = base / 2, f"half of {_fmt(base)} → {_fmt(base / 2)}"
    else:
        return None
    if not _money_ok(est, expected):
        return None
    return f"{pct}% of {_fmt(base)}: {how}."


def strat_tip_15(p, expected):
    """15% tip = 10% + half of that."""
    amt = p.get("bill_amount") or p.get("charge") or p.get("amount")
    if amt is None:
        return None
    ten = amt * 0.10
    tip = ten + ten / 2
    if abs(round(tip) - expected) > 1:
        return None
    return (f"15% of ${_fmt(amt)}: 10% is ${_fmt(round(ten, 2))}, half again is "
            f"${_fmt(round(ten / 2, 2))} → ${_fmt(round(tip, 2))} ≈ ${_fmt(round(tip))}.")


def strat_split_round_friendly(p, expected):
    """Split bill: nudge the total to the nearest multiple of the party size."""
    total, people = p.get("bill_amount") or p.get("total"), p.get("people")
    if not total or not people:
        return None
    friendly = round(total / people) * people
    share = friendly / people
    if not _money_ok(share, expected):
        return None
    nudge = "" if friendly == total else f" nudge to ${_fmt(friendly)} (a multiple of {people}) →"
    return (f"${_fmt(total)} ÷ {people}:{nudge} ${_fmt(share)} each"
            f"{'' if friendly == total else ' — inside the $1/1% tolerance'}.")


def strat_round_then_total(p, expected):
    """Multi-addend money totals: round each to whole dollars, track the drift."""
    amounts = p.get("amounts")
    if not amounts or len(amounts) < 2:
        return None
    rounded = [round(x) for x in amounts]
    est = sum(rounded)
    if not _money_ok(est, expected):
        return None
    parts = " + ".join(str(r) for r in rounded)
    return (f"Round each to whole dollars first: {parts} = {est} (exact "
            f"{_fmt(expected)}, inside tolerance). Never add cents in your head.")


def strat_round_both_subtract(p, expected):
    """category_difference: round both to the nearest ten, subtract left to right."""
    x, y = p.get("amount_a"), p.get("amount_b")
    if x is None or y is None:
        return None
    hi, lo = max(x, y), min(x, y)
    rh, rl = round(hi / 10) * 10, round(lo / 10) * 10
    est = rh - rl
    if not _money_ok(est, expected):
        return None
    return (f"{_fmt(hi)}-{_fmt(lo)}: drop cents, round to tens → {_fmt(rh)}-{_fmt(rl)}. "
            f"Then it's just {_fmt(rh / 10)}-{_fmt(rl / 10)} tens = {_fmt(est)} "
            f"(exact {_fmt(expected)}, inside the $1/1% tolerance).")


# ----------------------------------------------------------- weather

def strat_f_to_c(p, expected):
    """F→C approximation as stated by the prompt: subtract 30, halve."""
    f = p.get("f") or p.get("fahrenheit") or p.get("temp_f") or p.get("high")
    if f is None:
        return None
    step = f - 30
    if step / 2 != expected:
        return None
    half = "half of an even number" if step % 2 == 0 else "halve the odd number"
    return (f"{f}°F: -30 → {step}, halve → {_fmt(step / 2)}°C. Do the -30 FIRST, "
            f"then check even/odd before halving ({half}).")


def strat_f_between(p, expected):
    """Temperature gap: count up from the low to the high."""
    if "high" in p and "low" in p:
        hi, lo = int(p["high"]), int(p["low"])
    elif "warmer" in p and "cooler" in p:
        hi, lo = int(p["warmer"]), int(p["cooler"])
    else:
        return None
    if hi < lo:
        return None
    assert hi - lo == expected
    return (f"high {hi}, low {lo}: don't subtract — count up from {lo}. "
            f"{lo}→{hi} is {hi - lo}°.")


# ----------------------------------------------------------- registry

REGISTRY: list[Strategy] = [
    # subtraction
    Strategy("count_up", ("sub.",), strat_count_up),
    Strategy("subtract_in_hops", ("sub.",), strat_subtract_in_hops),
    Strategy("left_to_right_sub", ("sub.",), strat_left_to_right_sub),
    Strategy("complement_100", ("sub.2d", "sub.3d", "sub.4d"), strat_complement_100),
    Strategy("sub_same_ones", ("sub.2d", "sub.3d"), strat_sub_same_ones),
    # addition
    Strategy("bridge_ten", ("add.within20", "add.bridge10"), strat_bridge_ten),
    Strategy("doubles_anchor", ("add.within20", "add.bridge10", "add.2d"), strat_doubles_anchor),
    Strategy("compensation_add", ("add.",), strat_compensation_add),
    Strategy("add_tens_then_ones", ("add.",), strat_add_tens_then_ones),
    # multiplication
    Strategy("times_9", ("mul.x9", "mul.x10", "mul.x11"), strat_times_9),
    Strategy("times_12", ("mul.x12", "mul.x11"), strat_times_12),
    Strategy("times_11", ("mul.x11",), strat_times_11),
    Strategy("times_4", ("mul.x4",), strat_times_4),
    Strategy("times_5", ("mul.x5",), strat_times_5),
    Strategy("times_8", ("mul.x8",), strat_times_8),
    Strategy("times_15", ("mul.x15",), strat_times_15),
    Strategy("times_20", ("mul.x20",), strat_times_20),
    Strategy("near_square", ("mul.x9", "mul.x12"), strat_near_square),
    # division
    Strategy("div_as_inverse", ("div.",), strat_div_as_inverse),
    # percent / money
    Strategy("pct_anchor", ("pct", "money.restaurant_tip_15", "money.category_amount"),
             strat_pct_anchor),
    Strategy("tip_15", ("money.restaurant_tip_15",), strat_tip_15),
    Strategy("split_round_friendly", ("money.split_bill",), strat_split_round_friendly),
    Strategy("round_then_total",
             ("money.charge_total", "money.recent_charge_total"), strat_round_then_total),
    Strategy("round_both_subtract", ("money.category_difference",), strat_round_both_subtract),
    # weather
    Strategy("f_to_c", ("weather.f_to_c_approx",), strat_f_to_c),
    Strategy("f_between", ("weather.daily_range", "weather.temp_delta"), strat_f_between),
]


def tips_for(skill_id: str, parameters: dict, expected: float,
             tolerance: "dict | None" = None) -> list[str]:
    """Classify, collect prefix-matching strategies, return up to 2 verified tips.

    Each build runs inside the try/except safety net: a decomposition that
    can't reproduce ``expected`` (AssertionError) or hits malformed params
    (Type/Key/ZeroDivision) is silently dropped and never shown.
    """
    taxon = classify(skill_id, parameters)
    if taxon is None:
        return []
    family = taxon.family
    out: list[str] = []
    for strat in REGISTRY:
        if not any(family.startswith(pref) for pref in strat.families):
            continue
        try:
            tip = strat.build(parameters, expected)
        except (AssertionError, TypeError, KeyError, ZeroDivisionError):
            continue
        if tip:
            out.append(tip)
            if len(out) >= 2:
                break
    return out
