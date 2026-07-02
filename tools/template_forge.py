"""Batch LLM template forge — drafts grounded mental-math problem phrasings.

Usage::

    uv run python tools/template_forge.py [--dry-run] [--review] [--n 20]

Context: recent Plaid transactions (money.py) and cached weather forecast
(weather.py) are loaded, pre-rounded, and fed to the LLM.  Every candidate
is validated deterministically before being appended to data/template_pool.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from server.forge_ops import OPS
from server.money import _swag, load_recent_plaid_transactions, load_transactions
from server.weather import load_forecast, load_locations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POOL_PATH = ROOT / "data" / "template_pool.json"

# Grounded operation sets (from skills.yaml)
MONEY_OPS: frozenset[str] = frozenset([
    "restaurant_tip_15",
    "split_bill",
    "charge_total",
    "category_difference",
    "category_amount",
])
WEATHER_OPS: frozenset[str] = frozenset([
    "temp_delta",
    "daily_range",
    "f_to_c_approx",
    "wind_delta",
])
ALL_GROUNDED_OPS: frozenset[str] = MONEY_OPS | WEATHER_OPS

# Number regex used for hallucination check
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# ---------------------------------------------------------------------------
# OpenAI client (lazy singleton, same pattern as server/stt.py)
# ---------------------------------------------------------------------------

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        _CLIENT = OpenAI()
    return _CLIENT


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a template-forge agent that creates grounded mental-math problem phrasings.
Your output MUST be valid JSON with exactly this structure:
{"candidates": [<candidate>, ...]}

Each candidate object MUST have these fields:
- "prompt": str — the math question in plain English, ≤180 chars, ending with "?"
- "skill_id": "money_arithmetic" or "weather_math"
- "operation": str — one of the grounded ops listed below
- "op": str — one of the forge ops listed below
- "args": array of numbers — the arguments for the chosen op
- "source": str — short label (e.g. "plaid.latest.json", "open-meteo")

FORGE OPS (the ONLY allowed "op" values — use exactly these strings):
  "sub"      : (a, b) → a - b
  "add_list" : (*xs)  → sum of all args
  "pct_of"   : (pct, base) → pct * base / 100
  "div"      : (a, b) → a / b
  "delta"    : (a, b) → abs(a - b)

GROUNDED OPS (the ONLY allowed "operation" values):
  money_arithmetic : restaurant_tip_15 | split_bill | charge_total | \
category_difference | category_amount
  weather_math     : temp_delta | daily_range | f_to_c_approx | wind_delta

CRITICAL RULES:
1. Every number ≥ 10 in the prompt MUST also appear in "args". Never invent
   numbers that aren't in "args".
2. Use only pre-rounded integers taken directly from the context rows. Do NOT
   invent amounts.
3. "args" must be valid inputs for the chosen op (e.g. "div" takes exactly
   2 args and the divisor must be non-zero).
4. Vary sentence framing across candidates — don't repeat the same structure.
5. The prompt must be a real question a person could answer in their head.
"""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------


def _build_context() -> dict[str, Any]:
    """Load pre-rounded transaction + forecast data for grounding the LLM."""
    # Money: prefer live Plaid, fall back to CSV
    money_rows = load_recent_plaid_transactions() or load_transactions()
    money_context = [
        {
            "payee": r.get("payee", ""),
            "category": r.get("category", ""),
            "amount": _swag(r["amount"]),   # pre-rounded, matches prompt numbers
            "when": r.get("when", ""),
        }
        for r in money_rows[:20]
    ]

    # Weather: first 3 locations, integers only
    locations = load_locations()
    weather_context = []
    for loc in locations[:3]:
        fc = load_forecast(loc)
        weather_context.append({
            "location": loc["name"],
            "dates": fc.get("dates", []),
            "t_max": [int(round(v)) for v in fc.get("t_max", [])],
            "t_min": [int(round(v)) for v in fc.get("t_min", [])],
            "wind_max": [int(round(v)) for v in fc.get("wind_max", [])],
        })

    return {"money": money_context, "weather": weather_context}


# ---------------------------------------------------------------------------
# LLM call (extracted so tests can patch it cleanly)
# ---------------------------------------------------------------------------


def _call_llm(context: dict[str, Any], n: int, model: str) -> list[dict]:
    """Call the OpenAI chat API and return the raw candidate list."""
    user_msg = (
        f"Generate {n} distinct mental-math problem candidates grounded in the "
        f"context below.\n\nContext:\n{json.dumps(context, indent=2)}"
    )
    resp = _client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0.8,
    )
    content = (resp.choices[0].message.content or "").strip()
    parsed = json.loads(content)
    return parsed.get("candidates", [])


# ---------------------------------------------------------------------------
# Deterministic validation
# ---------------------------------------------------------------------------


def _validate(candidate: dict, existing_prompts: set[str]) -> list[str]:
    """Return a list of rejection reasons; empty list means accepted."""
    reasons: list[str] = []

    prompt: str = candidate.get("prompt", "")
    op: str = candidate.get("op", "")
    args: list = candidate.get("args", [])
    operation: str = candidate.get("operation", "")

    # 1. op must be in OPS
    if op not in OPS:
        reasons.append(f"unknown_op:{op!r}")
        return reasons  # can't validate computation without a valid op

    # 2. OPS[op](*args) must compute without error
    try:
        OPS[op](*args)
    except Exception as exc:
        reasons.append(f"compute_error:{exc}")
        return reasons  # args are unusable; skip number check

    # 3. Every number token ≥ 10 in prompt must appear in args
    args_as_floats = {float(a) for a in args}
    for tok in _NUMBER_RE.findall(prompt):
        val = float(tok)
        if val >= 10 and val not in args_as_floats:
            reasons.append(f"hallucinated_number:{tok}")

    # 4. operation must be a known grounded op
    if operation not in ALL_GROUNDED_OPS:
        reasons.append(f"unknown_operation:{operation!r}")

    # 5. prompt ≤ 180 chars, ends with ?
    if len(prompt) > 180:
        reasons.append(f"prompt_too_long:{len(prompt)}")
    if not prompt.endswith("?"):
        reasons.append("prompt_no_question_mark")

    # 6. Not a duplicate of an existing pool entry (case-folded)
    if prompt.casefold() in existing_prompts:
        reasons.append("duplicate_prompt")

    return reasons


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


def _candidate_id(prompt: str) -> str:
    """Deterministic 12-hex ID: SHA1 of case-folded prompt."""
    return hashlib.sha1(prompt.casefold().encode()).hexdigest()[:12]


def _load_pool(pool_path: Path) -> list[dict]:
    if not pool_path.exists():
        return []
    try:
        return json.loads(pool_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _make_entry(candidate: dict) -> dict:
    """Build a pool entry from a validated candidate."""
    return {
        "id": _candidate_id(candidate["prompt"]),
        "prompt": candidate["prompt"],
        "skill_id": candidate["skill_id"],
        "operation": candidate["operation"],
        "op": candidate["op"],
        "args": list(candidate["args"]),
        "source": candidate.get("source", "forge"),
        "created_at": time.time(),
        "used": False,
    }


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------


def run(
    n: int = 20,
    pool_path: Path = POOL_PATH,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Run the forge.

    Returns ``(accepted_entries, rejected_reasons_map)`` where the map keys
    are candidate prompts (or repr for prompt-less candidates) and values are
    lists of rejection reason strings.
    """
    model = os.environ.get("FEYNMAN_FORGE_MODEL", "gpt-4o-mini")

    if context is None:
        context = _build_context()

    pool = _load_pool(pool_path)
    existing_prompts: set[str] = {e["prompt"].casefold() for e in pool}

    raw_candidates = _call_llm(context, n, model)

    accepted: list[dict] = []
    rejected: dict[str, list[str]] = {}
    # Track prompts accepted in this batch to catch within-batch duplicates
    batch_prompts: set[str] = set()

    for cand in raw_candidates:
        prompt = cand.get("prompt", "")
        combined_existing = existing_prompts | batch_prompts
        reasons = _validate(cand, combined_existing)
        if reasons:
            rejected[prompt or repr(cand)] = reasons
        else:
            entry = _make_entry(cand)
            accepted.append(entry)
            batch_prompts.add(prompt.casefold())

    return accepted, rejected


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description="Template forge: batch LLM problem generator"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print but write nothing")
    parser.add_argument("--review", action="store_true", help="Ask y/n before writing")
    parser.add_argument("--n", type=int, default=20, help="Candidates to request")
    args = parser.parse_args()

    accepted, rejected = run(n=args.n, pool_path=POOL_PATH)

    # Flatten all rejection reasons for the summary line
    all_reasons: list[str] = []
    for reasons in rejected.values():
        all_reasons.extend(reasons)
    print(
        f"accepted {len(accepted)} / rejected {len(rejected)}"
        f" (reasons: {', '.join(all_reasons) if all_reasons else 'none'})"
    )

    for entry in accepted:
        print(f"  [{entry['skill_id']}] {entry['prompt']}")

    if args.dry_run:
        return

    if args.review and accepted:
        answer = input("Write accepted entries to pool? [y/n] ").strip().lower()
        if answer != "y":
            print("Skipped.")
            return

    # Append accepted entries to the pool file
    pool_path = POOL_PATH
    pool = _load_pool(pool_path)
    pool.extend(accepted)
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(json.dumps(pool, indent=2))
    print(f"Wrote {len(accepted)} entries to {pool_path}")


if __name__ == "__main__":
    main()
