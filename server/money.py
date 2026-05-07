"""CSV-backed money arithmetic problem generation."""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).parent.parent
DEFAULT_CSV = ROOT / "data" / "transactions.csv"
EXCLUDED_PRACTICE_PREFIXES = (
    "Home:",
    "Taxes:",
    "Personal Income",
    "Transfer",
    "Uncategorized",
)


def load_transactions(path: Path = DEFAULT_CSV) -> list[dict]:
    """Load included expenses from a Simplifi-style transaction CSV."""
    if not path.exists():
        return []
    rows = []
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            excluded = (r.get("Exclusion") or "").strip().lower() == "yes"
            amount = _money(r.get("Amount", "0"))
            if excluded or amount >= 0:
                continue
            rows.append({
                "date": _date_key(r.get("Date", "")),
                "payee": (r.get("Payee") or "").strip(),
                "category": (r.get("Category") or "Uncategorized").strip(),
                "amount": float(abs(amount)),
                "excluded": False,
            })
    return rows


def generate_problem(rows: list[dict] | None = None, target: dict | None = None) -> dict:
    rows = rows if rows is not None else load_transactions()
    if not rows:
        return _fallback_problem()
    operation = (target or {}).get("operation") or random.choice([
        "charge_total",
        "category_difference",
        "category_share",
    ])
    if operation == "charge_total":
        return _charge_total(rows)
    if operation == "category_difference":
        return _category_difference(rows)
    if operation == "category_share":
        return _category_share(rows)
    return _charge_total(rows)


def _charge_total(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        key = row["payee"] or row["category"]
        groups[key].append(row)
    candidates = [xs for xs in groups.values() if len(xs) >= 2]
    sample = random.choice(candidates) if candidates else sorted(rows, key=lambda r: r["amount"], reverse=True)[:2]
    sample = sorted(sample[:3], key=lambda r: r["date"])
    label = _clean_label(sample[0]["payee"], sample[0]["category"])
    swagged = [_swag(r["amount"]) for r in sample]
    total = sum(swagged)
    amount_text = _join_swagged(swagged)
    return {
        "prompt": f"You had {len(swagged)} charges at {label}: {amount_text}. About what's the total?",
        "expected": float(total),
        "parameters": {
            "operation": "charge_total",
            "source": "transactions.csv",
            "payee": label,
            "category": sample[0]["category"],
            "count": len(swagged),
            "amounts": swagged,
        },
    }


def _category_difference(rows: list[dict]) -> dict:
    practice_rows = _practice_rows(rows)
    month, month_rows = _pick_month(practice_rows)
    totals = _category_totals(month_rows)
    if len(totals) < 2:
        return _charge_total(rows)
    (cat_a, total_a), (cat_b, total_b) = sorted(
        totals.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )[:2]
    swag_a, swag_b = _swag(total_a), _swag(total_b)
    if swag_a == swag_b:
        # Don't ask "how much more" when they round to the same number.
        return _charge_total(rows)
    diff = abs(swag_a - swag_b)
    label_a, label_b = _category_leaf(cat_a), _category_leaf(cat_b)
    return {
        "prompt": (
            f"In {_month_label(month)}, you spent about ${swag_a:,} on {label_a} "
            f"and about ${swag_b:,} on {label_b}. How much more on {label_a}?"
        ),
        "expected": float(diff),
        "parameters": {
            "operation": "category_difference",
            "source": "transactions.csv",
            "month": month,
            "category_a": cat_a,
            "category_b": cat_b,
            "amount_a": swag_a,
            "amount_b": swag_b,
        },
    }


def _category_share(rows: list[dict]) -> dict:
    practice_rows = _practice_rows(rows)
    month, month_rows = _pick_month(practice_rows)
    totals = _category_totals(month_rows)
    if len(totals) < 2:
        return _charge_total(rows)
    total_swag = _swag(sum(totals.values()))
    category, amount = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[0]
    amount_swag = _swag(amount)
    pct = round(amount_swag / total_swag * 100) if total_swag else 0
    label = _category_leaf(category)
    return {
        "prompt": (
            f"In {_month_label(month)}, {label} was about ${amount_swag:,} out of ${total_swag:,} "
            "in tracked expenses. About what percent?"
        ),
        "expected": float(pct),
        "parameters": {
            "operation": "category_share",
            "source": "transactions.csv",
            "month": month,
            "category": category,
            "category_amount": amount_swag,
            "total_amount": total_swag,
        },
    }


def _category_totals(rows: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        totals[row["category"]] += row["amount"]
    return {k: _round_money(v) for k, v in totals.items() if v > 0}


def _practice_rows(rows: list[dict]) -> list[dict]:
    filtered = [
        row for row in rows
        if not row["category"].startswith(EXCLUDED_PRACTICE_PREFIXES)
    ]
    return filtered or rows


def _pick_month(rows: list[dict]) -> tuple[str, list[dict]]:
    by_month = defaultdict(list)
    for row in rows:
        month = row.get("date", "")[:7]
        if month:
            by_month[month].append(row)
    candidates = [
        (month, xs)
        for month, xs in by_month.items()
        if len({r["category"] for r in xs}) >= 2
    ]
    if not candidates:
        return ("all time", rows)
    return random.choice(candidates)


def _month_label(month: str) -> str:
    if month == "all time":
        return "all tracked transactions"
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def _fallback_problem() -> dict:
    return {
        "prompt": "What is 20 percent of 50?",
        "expected": 10.0,
        "parameters": {"operation": "category_share", "source": "fallback"},
    }


def _date_key(s: str) -> str:
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return s.strip()


def _money(s) -> Decimal:
    return Decimal(str(s).strip())


def _round_money(n) -> float:
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _swag(amount) -> int:
    """Round an amount to a clean ballpark integer.

    Rule: < $100 → nearest dollar; $100–$1000 → nearest $10;
    $1000–$10k → nearest $100; > $10k → nearest $500.
    Pre-rounded numbers in the prompt + integer expected = deterministic
    grading without forcing the user to recall cents.
    """
    n = abs(float(amount))
    if n < 100:
        unit = 1
    elif n < 1000:
        unit = 10
    elif n < 10000:
        unit = 100
    else:
        unit = 500
    return int(round(n / unit) * unit)


def _join_swagged(amounts: list[int]) -> str:
    fmt = lambda n: f"${n:,}"
    if len(amounts) == 1:
        return fmt(amounts[0])
    if len(amounts) == 2:
        return f"{fmt(amounts[0])} and {fmt(amounts[1])}"
    return ", ".join(fmt(a) for a in amounts[:-1]) + f", and {fmt(amounts[-1])}"


_PAYEE_NOISE = ("Web Pay", "Online Bill", "ACH", "Direct Dep", "Bill Pay")


def _clean_label(payee: str | None, category: str | None) -> str:
    """Pick a TTS-friendly label for a charge group.

    Bank exports often hand back things like '155 W. 21st St.-Web Pay-155W21-10D-Cu'.
    Speak the category leaf instead of that. We accept the payee only when it
    looks like a real name: no dashes, ≤25 chars after stripping known
    payment-method noise.
    """
    p = (payee or "").strip()
    for noise in _PAYEE_NOISE:
        p = p.replace(noise, "").strip()
    # If still has dashes (typical bank-coded payee), drop everything after
    # the first dash and check whether what remains is short and clean.
    if "-" in p:
        p = p.split("-", 1)[0].strip().rstrip(".")
    if p and len(p) <= 25 and not _looks_like_code(p):
        return p
    return _category_leaf(category) or "this charge"


def _category_leaf(category: str | None) -> str:
    """Strip parent prefixes ('Dining & Drinks:Restaurants' → 'Restaurants')."""
    if not category:
        return ""
    return category.split(":")[-1].strip()


def _looks_like_code(s: str) -> bool:
    """Heuristic: too many digits or all-caps fragments to be a real merchant name."""
    digits = sum(1 for c in s if c.isdigit())
    return digits > len(s) // 3
