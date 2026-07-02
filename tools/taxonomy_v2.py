#!/usr/bin/env python3
"""Taxonomy v2 prototype: classify attempts into primitive facts vs compound
procedures, assess strengths/weaknesses per taxon, and demo templated
strategy tips against real weak/slow attempts.

Read-only against data/feynman.db. This is the B1/B2 stress test from
docs/superpowers/plans/2026-07-01-ai-vs-code-plan-of-attack.md — the
classify() function is the candidate fact_key_v2, and STRATEGIES is the seed
of the strategy library. Every rendered tip's arithmetic is asserted against
the attempt's expected answer before it is shown.
"""
import json
import sqlite3
import statistics
from pathlib import Path

from server.taxonomy import classify, family_display, PRIMITIVE_ONSET_TARGET_MS

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"


# ------------------------------------------------------------- assessment

def assess(rows: list[sqlite3.Row]) -> dict:
    """Group classified attempts by family; verdict per family."""
    fams: dict[str, dict] = {}
    for r in rows:
        t = r["taxon"]
        f = fams.setdefault(t.family, {"tier": t.tier, "attempts": [], "keys": {}})
        f["attempts"].append(r)
        f["keys"].setdefault(t.key, []).append(r)

    report = {}
    for fam, f in fams.items():
        live = [r for r in f["attempts"] if not r["skipped"]]
        n = len(live)
        if n == 0:
            continue
        acc = sum(r["correct"] or 0 for r in live) / n
        lat_field = "onset_latency_ms" if f["tier"] == "primitive" else "resolution_latency_ms"
        lats = [r[lat_field] for r in live if r["correct"] and r[lat_field] is not None]
        med = int(statistics.median(lats)) if lats else None
        target = (PRIMITIVE_ONSET_TARGET_MS if f["tier"] == "primitive"
                  else (live[0]["target_latency_ms"] or 9000))
        if n < 5:
            verdict = "insufficient-n"
        elif acc >= 0.9 and med is not None and med <= target:
            verdict = "strong"
        elif acc >= 0.85:
            verdict = "slow" if (med or 0) > target else "strong"
        elif acc < 0.7:
            verdict = "weak"
        else:
            verdict = "shaky"
        # per-key outliers: any fact missed at least once with n>=2
        outliers = []
        for key, krows in sorted(f["keys"].items()):
            klive = [r for r in krows if not r["skipped"]]
            misses = sum(1 for r in klive if not r["correct"])
            if misses and len(klive) >= 1:
                outliers.append((key, len(klive), misses))
        report[fam] = {
            "tier": f["tier"], "n": n, "acc": acc, "median_ms": med,
            "target_ms": target, "verdict": verdict, "skips": len(f["attempts"]) - n,
            "outliers": sorted(outliers, key=lambda o: -o[2])[:8],
        }
    return report


# -------------------------------------------------------- strategy library

def _fmt(x: float) -> str:
    return f"{x:g}" if x != int(x) else str(int(x))


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


def strat_bridge_ten(p, expected):
    """Single-digit addition crossing 10: fill to 10, add the rest."""
    a, b = sorted([int(p["a"]), int(p["b"])], reverse=True)
    if not (a < 10 and a + b > 10 and b > 1):
        return None
    fill = 10 - a
    rest = b - fill
    assert 10 + rest == expected
    return (f"{a}+{b}: fill to ten. {a}+{fill}=10, then +{rest}={10 + rest}.")


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


def _money_ok(est, expected) -> bool:
    """The actual grading tolerance for money skills: max($1, 1% of expected)."""
    return abs(est - expected) <= max(1.0, 0.01 * abs(expected))


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


STRATEGIES = {
    # primitives — exact match
    "sub.within20": [strat_count_up, strat_left_to_right_sub],
    "sub.borrow20": [strat_count_up, strat_subtract_in_hops],
    "add.within20": [strat_bridge_ten],  # no-carry primitives ≤20 (was add.within10)
    "add.bridge10": [strat_bridge_ten, strat_compensation_add],
    # compound add/sub — prefix "add." / "sub." catches all new-style families
    # (e.g. sub.3d-2d.bt, add.2d+2d.co).  Each strategy fn filters by its own
    # conditions, so merging the old .n / .b / .c lists is safe.
    "sub.": [strat_left_to_right_sub, strat_subtract_in_hops, strat_count_up],
    "add.": [strat_compensation_add, strat_add_tens_then_ones],
    # multiplication — exact match on table families
    "mul.x9": [strat_times_9, strat_near_square],
    "mul.x12": [strat_times_12, strat_near_square],
    "mul.x10": [strat_times_9],  # 9x10/9x11 misses live here after sorting
    "mul.x11": [strat_times_9, strat_times_12],
    # money / weather — exact match
    "money.restaurant_tip_15": [strat_tip_15],
    "money.split_bill": [strat_split_round_friendly],
    "money.charge_total": [strat_round_then_total],
    "money.recent_charge_total": [strat_round_then_total],
    "money.category_difference": [strat_round_both_subtract],
    "weather.f_to_c_approx": [strat_f_to_c],
}


def tips_for(row) -> list[str]:
    fam = row["taxon"].family
    # Exact match first; then longest prefix key (keys ending with ".").
    strats = STRATEGIES.get(fam)
    if strats is None:
        prefix_keys = [k for k in STRATEGIES if k.endswith(".") and fam.startswith(k)]
        if prefix_keys:
            strats = STRATEGIES[max(prefix_keys, key=len)]
        else:
            strats = []
    out = []
    for strat in strats:
        try:
            tip = strat(row["params"], row["expected_answer"])
        except (AssertionError, TypeError, KeyError, ZeroDivisionError):
            continue  # decomposition didn't reproduce the expected answer — never show it
        if tip:
            out.append(tip)
    return out


# ------------------------------------------------------------------- main

def load_attempts() -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT a.*, s.target_latency_ms FROM attempts a
           LEFT JOIN skills s ON s.id = a.skill_id ORDER BY a.created_at"""
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            params = json.loads(r["parameters"] or "{}")
        except ValueError:
            params = {}
        taxon = classify(r["skill_id"], params)
        if taxon:
            d = dict(r)
            d["params"], d["taxon"] = params, taxon
            out.append(d)
    return out


if __name__ == "__main__":
    rows = load_attempts()
    print(f"classified {len(rows)} attempts\n")
    report = assess(rows)

    for tier in ("primitive", "compound"):
        print(f"=== {tier.upper()} FAMILIES ===")
        fams = {k: v for k, v in report.items() if v["tier"] == tier}
        for fam, s in sorted(fams.items(), key=lambda kv: (kv[1]["acc"], -kv[1]["n"])):
            med = f"{s['median_ms']}ms" if s["median_ms"] else "—"
            line = (f"{fam:28s} ({family_display(fam)}) "
                    f"n={s['n']:<4d} acc={s['acc']:.2f} med={med:>8s} "
                    f"tgt={s['target_ms']}ms skips={s['skips']} -> {s['verdict']}")
            print(line)
            for key, kn, miss in s["outliers"]:
                print(f"    miss: {key} ({miss}/{kn} wrong)")
        print()

    print("=== STRATEGY TIPS ON REAL FAILED/SLOW ATTEMPTS ===")
    shown = 0
    for r in rows:
        if r["skipped"]:
            continue
        lat_field = ("onset_latency_ms" if r["taxon"].tier == "primitive"
                     else "resolution_latency_ms")
        target = (PRIMITIVE_ONSET_TARGET_MS if r["taxon"].tier == "primitive"
                  else (r["target_latency_ms"] or 9000))
        wrong = not r["correct"]
        slow = r["correct"] and (r[lat_field] or 0) > target * 1.5
        if not (wrong or slow):
            continue
        tips = tips_for(r)
        if not tips:
            continue
        mode = "WRONG" if wrong else f"SLOW ({r[lat_field]}ms)"
        print(f"\n[{mode}] {r['prompt_text']}  (key={r['taxon'].key})")
        for t in tips:
            print(f"   → {t}")
        shown += 1
    # coverage: how many weak/slow attempts got no tip
    weak_all = [r for r in rows if not r["skipped"] and (
        not r["correct"] or (r["correct"] and (r["resolution_latency_ms"] or 0) >
                             (r["target_latency_ms"] or 9000) * 1.5))]
    print(f"\ncoverage: {shown}/{len(weak_all)} weak/slow attempts got >=1 verified tip")
