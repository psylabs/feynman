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
    label = sample[0]["payee"] or sample[0]["category"]
    amounts = [_round_money(r["amount"]) for r in sample]
    total = _round_money(sum(amounts))
    amount_text = _join_money(amounts)
    return {
        "prompt": f"You had {len(amounts)} {label} charges: {amount_text}. What was the total?",
        "expected": total,
        "parameters": {
            "operation": "charge_total",
            "source": "transactions.csv",
            "payee": label,
            "category": sample[0]["category"],
            "count": len(amounts),
            "amounts": amounts,
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
    diff = _round_money(abs(total_a - total_b))
    return {
        "prompt": (
            f"In {_month_label(month)}, your {cat_a} spend was {_fmt_money(total_a)} and {cat_b} was "
            f"{_fmt_money(total_b)}. How much more was the larger category?"
        ),
        "expected": diff,
        "parameters": {
            "operation": "category_difference",
            "source": "transactions.csv",
            "month": month,
            "category_a": cat_a,
            "category_b": cat_b,
            "amount_a": total_a,
            "amount_b": total_b,
        },
    }


def _category_share(rows: list[dict]) -> dict:
    practice_rows = _practice_rows(rows)
    month, month_rows = _pick_month(practice_rows)
    totals = _category_totals(month_rows)
    if len(totals) < 2:
        return _charge_total(rows)
    total = _round_money(sum(totals.values()))
    category, amount = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[0]
    pct = round((amount / total) * 100, 1) if total else 0.0
    return {
        "prompt": (
            f"In {_month_label(month)}, {category} was {_fmt_money(amount)} out of {_fmt_money(total)} "
            "in tracked expenses. About what percent was that?"
        ),
        "expected": pct,
        "parameters": {
            "operation": "category_share",
            "source": "transactions.csv",
            "month": month,
            "category": category,
            "category_amount": amount,
            "total_amount": total,
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


def _fmt_money(n: float) -> str:
    return f"${n:.2f}"


def _join_money(amounts: list[float]) -> str:
    if len(amounts) == 1:
        return _fmt_money(amounts[0])
    if len(amounts) == 2:
        return f"{_fmt_money(amounts[0])} and {_fmt_money(amounts[1])}"
    return ", ".join(_fmt_money(a) for a in amounts[:-1]) + f", and {_fmt_money(amounts[-1])}"
