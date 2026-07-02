"""Money arithmetic problem generation.

Category/charge-total problems are backed by the historical CSV. The two
"sticky" finance staples (15% tip, split-the-bill) pull from *recent* Plaid
transactions so they name a real merchant, amount, and day ("$725 at
DoNotDisturb yesterday") — recency is what makes them memorable.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from server import forge_ops, forge_pool

logger = logging.getLogger(__name__)


ROOT = Path(__file__).parent.parent
DEFAULT_CSV = ROOT / "data" / "transactions.csv"
PLAID_ENV = "FEYNMAN_PLAID_LATEST_JSON"
RECENT_WINDOW_DAYS = 7
FRIENDLY_PCTS = (10, 15, 20, 25, 30, 40, 50)
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


def load_recent_plaid_transactions(
    path: Path | str | None = None,
    window_days: int = RECENT_WINDOW_DAYS,
) -> list[dict]:
    """Load expenses from the recent window of the Plaid ``latest.json`` dump.

    Each row carries a human ``when`` label ("yesterday", "on Monday") computed
    relative to the dump's ``until`` date, so prompts can stay sticky. Returns
    ``[]`` when the env var is unset or the file is missing/unreadable; callers
    fall back to the historical CSV.
    """
    if path is None:
        configured = os.environ.get(PLAID_ENV)
        if not configured:
            return []
        path = configured
    path = Path(path).expanduser()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    transactions = payload.get("transactions")
    if not isinstance(transactions, list):
        return []
    until = _plaid_until_date(payload, transactions)
    if not until:
        return []
    cutoff = until - timedelta(days=window_days)

    rows = []
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        tx_date = _iso_date(tx.get("date"))
        if not tx_date or tx_date < cutoff or tx_date > until:
            continue
        try:
            amount = _money(tx.get("amount", "0"))
        except (InvalidOperation, ValueError):
            continue
        if amount <= 0:  # skip refunds/credits/income
            continue
        rows.append({
            "date": tx_date.isoformat(),
            "when": _relative_day(tx_date, until),
            "payee": (tx.get("merchant") or tx.get("name") or "").strip(),
            "category": (tx.get("category") or "Uncategorized").strip(),
            "amount": float(amount),
            "excluded": False,
        })
    return rows


def generate_problem(rows: list[dict] | None = None, target: dict | None = None) -> dict:
    operation = (target or {}).get("operation") or random.choice([
        "charge_total",
        "category_difference",
    ])
    # Try the forge pool first — LLM-generated prompts grounded on real data.
    entry = forge_pool.take("money_arithmetic", operation)
    if entry is not None:
        try:
            expected = forge_ops.OPS[entry["op"]](*entry["args"])
            return {
                "prompt": entry["prompt"],
                "expected": float(expected),
                "parameters": {
                    "operation": entry["operation"],
                    "source": "forge",
                    "forge_id": entry["id"],
                    "level": (target or {}).get("level"),
                },
            }
        except Exception:
            logger.warning(
                "forge: malformed pool entry %s skipped (op=%r args=%r)",
                entry.get("id"), entry.get("op"), entry.get("args"),
            )
    # Sticky finance staples pull from recent Plaid data (real merchant + day);
    # fall back to the historical CSV only when Plaid is unavailable.
    if operation in ("restaurant_tip_15", "split_bill"):
        finance_rows = rows if rows is not None else (
            load_recent_plaid_transactions() or load_transactions()
        )
        if not finance_rows:
            return _fallback_problem()
        # A specific charge may be pinned by the scheduler so the two finance
        # slots in one session don't land on the same merchant.
        charge = (target or {}).get("charge")
        if operation == "restaurant_tip_15":
            return _restaurant_tip_15(finance_rows, charge=charge)
        return _split_bill(
            finance_rows,
            max_party=(target or {}).get("max_party", SPLIT_BILL_BASE_MAX),
            charge=charge,
        )

    rows = rows if rows is not None else load_transactions()
    if not rows:
        return _fallback_problem()
    if operation == "charge_total":
        return _charge_total(rows)
    if operation == "category_difference":
        return _category_difference(rows)
    if operation == "category_amount":
        return _category_amount(rows, level=(target or {}).get("level"))
    return _charge_total(rows)


# ---- finance: tip + split-the-bill ---------------------------------------

TIP_PERCENT = 15
# Split-the-bill party sizes. Base (3-5) is always available; advanced (6-8)
# unlocks once the scheduler decides the user is fluent at the base set and
# raises `max_party` to SPLIT_BILL_ADVANCED_MAX.
SPLIT_BILL_BASE_DIVISORS = (3, 4, 5)
SPLIT_BILL_ADVANCED_DIVISORS = (6, 7, 8)
SPLIT_BILL_BASE_MAX = 5
SPLIT_BILL_ADVANCED_MAX = 8
SPLIT_MIN_SHARE = 3  # don't generate splits where each person owes < $3


def _dining_pool(rows: list[dict]) -> list[dict]:
    return [r for r in rows if _is_restaurant_category(r.get("category", ""))] or rows


def pick_finance_charges(rows: list[dict] | None = None) -> dict:
    """Pick two DISTINCT recent dining charges for the tip and split slots.

    Returns ``{"restaurant_tip_15": row|None, "split_bill": row|None}``. The two
    rows are different merchants whenever possible, so a single session never
    repeats the same restaurant across its finance problems. Selection is
    uniform among eligible charges (no amount weighting) to keep variety across
    sessions. The split charge is drawn from bills big enough for a non-trivial
    split.
    """
    if rows is None:
        rows = load_recent_plaid_transactions() or load_transactions()
    dining = _dining_pool(rows)
    if not dining:
        return {"restaurant_tip_15": None, "split_bill": None}
    split_pool = [r for r in dining if r["amount"] >= SPLIT_MIN_SHARE * min(SPLIT_BILL_BASE_DIVISORS)] or dining
    split_row = random.choice(split_pool)
    tip_pool = [r for r in dining if _label_key(r) != _label_key(split_row)] or dining
    tip_row = random.choice(tip_pool)
    return {"restaurant_tip_15": tip_row, "split_bill": split_row}


def _label_key(row: dict) -> str:
    return _clean_label(row.get("payee"), row.get("category")).casefold()


def _restaurant_tip_15(rows: list[dict], charge: dict | None = None) -> dict:
    """Ask for a 15% tip on a real recent restaurant bill (rounded to $1)."""
    row = charge or random.choice(_dining_pool(rows))
    label = _clean_label(row["payee"], row["category"])
    bill = _swag(row["amount"])
    tip = _round_tip(bill, TIP_PERCENT)
    return {
        "prompt": (
            f"{_spent_clause(label, bill, row.get('when'))} "
            f"What is a {TIP_PERCENT} percent tip, rounded to the nearest dollar?"
        ),
        "expected": float(tip),
        "parameters": {
            "operation": "restaurant_tip_15",
            "source": row.get("source", "plaid.latest.json"),
            "label": label,
            "category": row["category"],
            "when": row.get("when"),
            "bill_amount": bill,
            "tip_percent": TIP_PERCENT,
        },
    }


def _split_bill(rows: list[dict], max_party: int = SPLIT_BILL_BASE_MAX,
                charge: dict | None = None) -> dict:
    """Split a real recent charge evenly among N people; ask for each share.

    Party size is drawn from the divisors allowed by `max_party`. The bill is
    the real amount rounded to the nearest clean multiple of `people`, so it
    stays sticky (real merchant/day) but divides evenly for mental math.
    """
    # You split restaurant/bar checks, not a Lyft or a loan payment. Prefer
    # dining charges big enough that the split isn't trivial; pick uniformly so
    # variety isn't collapsed onto the single largest bill.
    if charge is not None:
        row = charge
    else:
        dining = _dining_pool(rows)
        pool = [r for r in dining if r["amount"] >= SPLIT_MIN_SHARE * min(SPLIT_BILL_BASE_DIVISORS)] or dining
        row = random.choice(pool)
    label = _clean_label(row["payee"], row["category"])
    allowed = [
        d for d in SPLIT_BILL_BASE_DIVISORS + SPLIT_BILL_ADVANCED_DIVISORS
        if d <= max_party
    ] or list(SPLIT_BILL_BASE_DIVISORS)
    # Don't split a $15 lunch 8 ways: keep each share at least SPLIT_MIN_SHARE,
    # falling back to the smallest party size for genuinely tiny bills.
    divisors = [d for d in allowed if round(row["amount"] / d) >= SPLIT_MIN_SHARE] or [min(allowed)]
    people = random.choice(divisors)
    share = max(1, round(row["amount"] / people))
    bill = share * people
    return {
        "prompt": (
            f"{_spent_clause(label, bill, row.get('when'))} "
            f"Split the check {people} ways — about how much is each share?"
        ),
        "expected": float(share),
        "parameters": {
            "operation": "split_bill",
            "source": row.get("source", "plaid.latest.json"),
            "label": label,
            "category": row["category"],
            "when": row.get("when"),
            "bill_amount": bill,
            "people": people,
            "max_party": max_party,
        },
    }


def _spent_clause(label: str, amount: int, when: str | None) -> str:
    """'You spent $725 at DoNotDisturb yesterday.' (drops the day if unknown)."""
    when_text = f" {when}" if when else ""
    return f"You spent ${amount:,} at {label}{when_text}."


def _is_restaurant_category(category: str) -> bool:
    normalized = (category or "").upper().replace(" ", "_")
    return "FOOD" in normalized or "DINING" in normalized or "RESTAURANT" in normalized


def _round_tip(amount: int, percent: int) -> int:
    raw = Decimal(amount) * Decimal(percent) / Decimal(100)
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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


def _snap_pct(true_pct: float) -> int | None:
    """Snap true_pct to the nearest friendly percent; return None if >4pp away."""
    best = min(FRIENDLY_PCTS, key=lambda p: abs(p - true_pct))
    if abs(best - true_pct) <= 4:
        return best
    return None


def _category_amount(rows: list[dict], level=None) -> dict:
    """Ask how many dollars a friendly-percent slice of monthly spend amounts to.

    Picks a (month, category) pair whose true share snaps within 4 percentage
    points of a friendly percent (10/15/20/25/30/40/50). Falls back to
    _charge_total if no qualifying candidate is found in 10 tries.
    """
    practice_rows = _practice_rows(rows)
    tries = 0
    while tries < 10:
        month, month_rows = _pick_month(practice_rows)
        totals = _category_totals(month_rows)
        if not totals:
            tries += 1
            continue
        month_total = sum(totals.values())
        if month_total <= 0:
            tries += 1
            continue
        total_swag = _swag(month_total)
        candidates = list(totals.items())
        random.shuffle(candidates)
        for category, amount in candidates:
            tries += 1
            true_pct = amount / month_total * 100
            pct = _snap_pct(true_pct)
            if pct is not None:
                label = _category_leaf(category)
                expected = total_swag * pct / 100
                return {
                    "prompt": (
                        f"In {_month_label(month)}, about {pct}% of your "
                        f"${total_swag:,} spend was {label}. About how much is that?"
                    ),
                    "expected": float(expected),
                    "parameters": {
                        "operation": "category_amount",
                        "percentage": pct,
                        "total": total_swag,
                        "category": category,
                        "month": month,
                        "level": level,
                    },
                }
            if tries >= 10:
                break
    return _charge_total(rows)


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
        "parameters": {"operation": "charge_total", "source": "fallback"},
    }


def _date_key(s: str) -> str:
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return s.strip()


def _iso_date(s) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _plaid_until_date(payload: dict, transactions: list[dict]) -> date | None:
    until = _iso_date(payload.get("until"))
    if until:
        return until
    dates = [_iso_date(tx.get("date")) for tx in transactions if isinstance(tx, dict)]
    dates = [d for d in dates if d]
    return max(dates) if dates else None


def _relative_day(tx_date: date, until: date) -> str:
    """Human day label relative to the dump's reference date."""
    delta = (until - tx_date).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta < 7:
        return f"on {tx_date.strftime('%A')}"
    return "this week"


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
