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

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

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

# Number regex (includes optional leading minus for negative numbers)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# Validator constants
_FRIENDLY_PCTS: frozenset[int] = frozenset({5, 10, 15, 20, 25, 30, 40, 50, 75})
_MAX_ADDENDS: int = 4
_MAX_EXPECTED: float = 100_000.0
_PROMPT_MAX_CHARS: int = 100

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
- "prompt": str — ≤90 chars, direct second-person voice, one sentence + question, ends with "?"
- "skill_id": "money_arithmetic" or "weather_math"
- "operation": str — one of the grounded ops listed below
- "op": str — one of the forge ops listed below
- "args": array of numbers — the arguments for the chosen op
- "source": str — short label (e.g. "plaid.latest.json", "open-meteo")

FORGE OPS (the ONLY allowed "op" values):
  "sub"      : (a, b) → a - b
  "add_list" : (*xs)  → sum of all args (max 4 addends)
  "pct_of"   : (pct, base) → pct * base / 100
  "div"      : (a, b) → a / b  (must divide evenly — integer result only)
  "delta"    : (a, b) → abs(a - b)

GROUNDED OPS (the ONLY allowed "operation" values):
  money_arithmetic : restaurant_tip_15 | split_bill | charge_total | \
category_difference | category_amount
  weather_math     : temp_delta | daily_range | f_to_c_approx | wind_delta

HARD RULES (the validator enforces every one — violating any causes rejection):
1. prompt ≤ 90 chars, ends with "?", no filler phrases ("If you...", "What is the total...").
2. Every arg number MUST appear literally in the prompt. No hiding numbers behind words like "half".
3. The prompt must contain ≥ 2 numeric tokens.
4. Use ONLY numbers from the provided context rows — do NOT invent numbers.
5. "div": args must divide evenly — integer quotient required.
6. "sub": result must be positive (first arg > second arg).
7. "pct_of": percent must be one of 5/10/15/20/25/30/40/50/75; result must be a multiple of 0.5.
8. "add_list": at most 4 addends.
9. Answer must be in the range (0, 100000].
10. weather_math prompts must NOT contain "$". money_arithmetic must NOT contain "°" or "mph".
11. Vary sentence structure — do not repeat the same framing across candidates.

GOOD EXAMPLES (short, direct, readable aloud in ~4 seconds):
✓ "Amazon $36, PRO $22. What's the difference?" [money, delta, args=[36,22]]
✓ "Chipotle $120. What's a 15% tip?" [money, pct_of, args=[15,120]]
✓ "High 89, low 72. What's the range?" [weather, delta, args=[89,72]]

BAD EXAMPLES (rejected — do not imitate):
✗ "What is the total amount spent on transportation yesterday?" — no numbers, unanswerable
✗ "If you divide the $36 spent on Amazon by the $14 for entertainment, what do you get?" \
— 36÷14 is not an integer, and the prompt is too verbose
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


def _build_context_numbers(context: dict[str, Any]) -> dict[str, set[float]]:
    """Extract the set of numeric values available per skill domain."""
    numbers: dict[str, set[float]] = {"money_arithmetic": set(), "weather_math": set()}
    for row in context.get("money", []):
        amt = row.get("amount")
        if isinstance(amt, (int, float)):
            numbers["money_arithmetic"].add(float(amt))
    for loc in context.get("weather", []):
        for key in ("t_max", "t_min", "wind_max"):
            for v in loc.get(key, []):
                if isinstance(v, (int, float)):
                    numbers["weather_math"].add(float(v))
    return numbers


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


def _validate(
    candidate: dict,
    existing_prompts: set[str],
    context_numbers: dict[str, set[float]] | None = None,
) -> list[str]:
    """Return a list of rejection reasons; empty list means accepted."""
    reasons: list[str] = []

    prompt: str = candidate.get("prompt", "")
    op: str = candidate.get("op", "")
    args: list = candidate.get("args", [])
    operation: str = candidate.get("operation", "")
    skill_id: str = candidate.get("skill_id", "")

    # 1. op must be in OPS
    if op not in OPS:
        reasons.append(f"unknown_op:{op!r}")
        return reasons  # can't validate computation without a valid op

    # 2. OPS[op](*args) must compute without error
    try:
        expected = OPS[op](*args)
    except Exception as exc:
        reasons.append(f"compute_error:{exc}")
        return reasons  # args are unusable; skip further checks

    # 3. Answer range: must be in (0, 100000]
    if not (0 < expected <= _MAX_EXPECTED):
        reasons.append(f"ugly_answer:{expected}")

    # 4. Op-specific answer quality
    if op == "div":
        if expected % 1 != 0:
            reasons.append(f"div_not_integer:{expected}")
    elif op == "sub":
        if expected <= 0:
            reasons.append(f"sub_negative:{expected}")
    elif op == "pct_of":
        pct = float(args[0]) if args else 0.0
        if int(pct) not in _FRIENDLY_PCTS or pct != int(pct):
            reasons.append(f"pct_unfriendly:{pct}")
        else:
            # result must be a multiple of 0.5
            if abs(round(expected / 0.5) * 0.5 - expected) > 1e-9:
                reasons.append(f"pct_ugly_result:{expected}")
    elif op == "add_list":
        if len(args) > _MAX_ADDENDS:
            reasons.append(f"too_many_addends:{len(args)}")

    # 5. Domain separation
    if skill_id == "weather_math" and "$" in prompt:
        reasons.append("domain_mix:$ in weather_math")
    if skill_id == "money_arithmetic" and ("°" in prompt or "mph" in prompt.lower()):
        reasons.append("domain_mix:weather_token in money_arithmetic")

    # 6. Normalize comma-formatted numbers (e.g. "$1,250" → "1250") for number checks
    prompt_normalized = re.sub(r"(?<=\d),(?=\d)", "", prompt)
    all_number_tokens = _NUMBER_RE.findall(prompt_normalized)
    prompt_number_floats: set[float] = {float(t) for t in all_number_tokens}

    # 7. too_few_numbers: prompt must contain ≥ 2 numeric tokens
    if len(all_number_tokens) < 2:
        reasons.append(f"too_few_numbers:{len(all_number_tokens)}")

    # 8. args_not_in_prompt: every arg must appear as a numeric token in the prompt
    for arg in args:
        if float(arg) not in prompt_number_floats:
            reasons.append(f"args_not_in_prompt:{arg}")

    # 9. hallucinated_number: every number |val| ≥ 10 in prompt must appear in args
    args_as_floats = {float(a) for a in args}
    for tok in all_number_tokens:
        val = float(tok)
        if abs(val) >= 10 and val not in args_as_floats:
            reasons.append(f"hallucinated_number:{tok}")

    # 10. operation must be a known grounded op
    if operation not in ALL_GROUNDED_OPS:
        reasons.append(f"unknown_operation:{operation!r}")

    # 11. prompt ≤ 100 chars, ends with ?
    if len(prompt) > _PROMPT_MAX_CHARS:
        reasons.append(f"prompt_too_long:{len(prompt)}")
    if not prompt.endswith("?"):
        reasons.append("prompt_no_question_mark")

    # 12. Not a duplicate of an existing pool entry (case-folded)
    if prompt.casefold() in existing_prompts:
        reasons.append("duplicate_prompt")

    # 13. Context grounding: args must be a subset of context numbers for this skill
    #     (None = skip, used by tests that don't inject context)
    if context_numbers is not None:
        skill_ctx = context_numbers.get(skill_id, set())
        if skill_ctx:  # only enforce when context has numbers (non-empty)
            args_floats = {float(a) for a in args}
            if not args_floats.issubset(skill_ctx):
                missing = args_floats - skill_ctx
                reasons.append(f"args_not_in_context:{sorted(missing)}")

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
    context_numbers: dict[str, set[float]] | None = None,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Run the forge.

    Returns ``(accepted_entries, rejected_reasons_map)`` where the map keys
    are candidate prompts (or repr for prompt-less candidates) and values are
    lists of rejection reason strings.

    ``context_numbers`` maps skill_id → set of float values available in the
    context.  Pass ``None`` (the default) to skip context-grounding checks —
    tests that don't inject context use this.  Production callers (main) always
    build and pass context_numbers.
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
        reasons = _validate(cand, combined_existing, context_numbers)
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

    context = _build_context()
    context_numbers = _build_context_numbers(context)
    accepted, rejected = run(n=args.n, pool_path=POOL_PATH, context=context,
                              context_numbers=context_numbers)

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
