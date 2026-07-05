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

from server import suppressions
from server.forge_ops import OPS, features_for_entry
from server.money import (
    _FORGE_EXCLUDED_OPS,
    _swag,
    load_recent_plaid_transactions,
    load_transactions,
)
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
_FRIENDLY_PCTS: frozenset[int] = frozenset({5, 15, 20, 25, 30, 40, 50, 75})  # no 10% — decimal shift
_MAX_ADDENDS: int = 4
_MAX_EXPECTED: float = 100_000.0
_PROMPT_MAX_CHARS: int = 100
_MAX_PER_OP_BATCH: int = 3

# Compatibility table: which compute ops legitimately implement each grounded
# operation.  The `operation` label keys the FSRS item, so a mislabeled pair
# (e.g. an addition problem labeled split_bill) pollutes scheduling.
# f_to_c_approx uses the app's rough subtract-30-then-halve rule
# ((F - 30) / 2 ≈ C, see server/weather.py); a single flat op can only express
# one step, so either step encoding is accepted: "sub" for the F - 30 step or
# "div" for the halving step.
_OP_COMPAT: dict[str, frozenset[str]] = {
    "charge_total":        frozenset({"add_list"}),
    "category_difference": frozenset({"sub", "delta"}),
    "split_bill":          frozenset({"div"}),
    "restaurant_tip_15":   frozenset({"pct_of"}),
    "category_amount":     frozenset({"pct_of"}),
    "temp_delta":          frozenset({"sub", "delta"}),
    "daily_range":         frozenset({"sub", "delta"}),
    "wind_delta":          frozenset({"sub", "delta"}),
    "f_to_c_approx":       frozenset({"sub", "div"}),
}

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
- "operation": str — WHICH app problem type this is (grounded label, list below)
- "op": str — HOW to compute the answer (compute-registry name, list below)
- "args": array of numbers — the arguments for the chosen op
- "source": str — short label (e.g. "plaid.latest.json", "open-meteo")

"op" and "operation" are two DIFFERENT fields — never swap or conflate them.

"op" = how to compute — the ONLY allowed "op" values (compute registry):
  "sub"      : (a, b) → a - b
  "add_list" : (*xs)  → sum of all args (max 4 addends)
  "pct_of"   : (pct, base) → pct * base / 100
  "div"      : (a, b) → a / b  (must divide evenly — integer result only)
  "delta"    : (a, b) → abs(a - b)

"operation" = which app problem type this is — the ONLY allowed "operation" values:
  for skill_id money_arithmetic : charge_total | category_difference | category_amount
  for skill_id weather_math     : temp_delta | daily_range | f_to_c_approx | wind_delta

Never put a compute name ("sub", "add_list", "pct_of", "div", "delta") in the
"operation" field — that field takes ONLY the grounded labels above, and the
"op" field takes ONLY the five compute names.

LABELING: operation=charge_total for any "what's the total" over multiple
purchases. Do NOT emit restaurant_tip_15 or split_bill — those problem types
are generated elsewhere and will be rejected here.

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
12. No arg may be 0 — a zero operand makes the problem degenerate.

STYLE: write in the app's natural second-person voice, with recency/day texture —
start money prompts with "You spent" or a day reference where natural; never list
bare "LABEL $N, LABEL $N" pairs like a receipt.

DIVERSITY: aim for a mix of operations across the batch — include at least one
percentage problem (category_amount, op=pct_of with a friendly percent from
{5,10,15,20,25,30,40,50,75}) and at most 3 of any one operation.

GOOD EXAMPLES (complete candidate JSON — short, direct, readable aloud in ~4 seconds;
note how "operation" is a grounded label while "op" is a compute name):
✓ {"prompt": "You spent $36 at Amazon and $22 at PRO on Tuesday. What's the difference?",
   "skill_id": "money_arithmetic", "operation": "category_difference", "op": "delta",
   "args": [36, 22], "source": "plaid.latest.json"}
✓ {"prompt": "You spent $120 on dining this week. What's 25% of that?",
   "skill_id": "money_arithmetic", "operation": "category_amount", "op": "pct_of",
   "args": [25, 120], "source": "plaid.latest.json"}
✓ {"prompt": "You spent $12 at MTA, $36 at Amazon and $22 at PRO on Monday. What's the total?",
   "skill_id": "money_arithmetic", "operation": "charge_total", "op": "add_list",
   "args": [12, 36, 22], "source": "plaid.latest.json"}
✓ {"prompt": "Today's high is 89, low 75. What's the range?",
   "skill_id": "weather_math", "operation": "daily_range", "op": "delta",
   "args": [89, 75], "source": "open-meteo"}

BAD EXAMPLES (rejected — do not imitate):
✗ "What is the total amount spent on transportation yesterday?" — no numbers, unanswerable
✗ "If you divide the $36 spent on Amazon by the $14 for entertainment, what do you get?" \
— 36÷14 is not an integer, and the prompt is too verbose
✗ "Amazon $36, PRO $22. What's the difference?" — receipt-style bare "LABEL $N, LABEL $N" \
listing; not natural second-person phrasing (say "You spent $36 at Amazon and $22 at PRO..." instead)
✗ {"operation": "delta", "op": "delta", ...} — "delta" is a compute name; the "operation" \
field must be a grounded label like category_difference or daily_range
✗ {"operation": "split_bill", "op": "add_list", ...} — split_bill and restaurant_tip_15 \
are never forged (generated elsewhere); a multi-purchase total is charge_total
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

    # 4b. zero_arg: a 0 operand makes a degenerate problem (e.g. "You spent
    #     $36 at Amazon and $0 at Google Cloud. What's the difference?")
    for arg in args:
        if float(arg) == 0.0:
            reasons.append(f"zero_arg:{arg}")

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
    # 10b. (operation, op) pair must be compatible: the operation label keys
    #      the FSRS item, so e.g. an addition problem labeled split_bill would
    #      pollute scheduling.  See _OP_COMPAT for the table.
    elif op not in _OP_COMPAT.get(operation, frozenset()):
        reasons.append(f"op_operation_mismatch:{operation}/{op}")

    # 10c. operation must be consumable: server/money.py excludes some ops
    #      from pool consumption (_FORGE_EXCLUDED_OPS, live-Plaid protection,
    #      Task 6.2), so forging entries for them creates permanent dead
    #      weight in the pool.  Independent of the mismatch check above.
    if operation in _FORGE_EXCLUDED_OPS:
        reasons.append(f"op_not_consumable:{operation}")

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
    #
    #     Carve-outs: two op args are chosen constants, not context-derived
    #     amounts, so they are exempt from the context-membership check —
    #     every OTHER arg for these ops must still come from context:
    #       - pct_of arg[0] (the percent) when it's a friendly percent
    #         (5/10/15/20/25/30/40/50/75); the base (arg[1]) still must be
    #         grounded.
    #       - div arg[1] (the divisor) when it's a small party-size integer
    #         2..12; the dividend (arg[0]) still must be grounded.
    if context_numbers is not None:
        skill_ctx = context_numbers.get(skill_id, set())
        if skill_ctx:  # only enforce when context has numbers (non-empty)
            exempt_indices: set[int] = set()
            if op == "pct_of" and args:
                pct_val = float(args[0])
                if pct_val in _FRIENDLY_PCTS:
                    exempt_indices.add(0)
            elif op == "div" and len(args) > 1:
                divisor = float(args[1])
                if divisor.is_integer() and 2 <= int(divisor) <= 12:
                    exempt_indices.add(1)
            args_floats = {float(a) for i, a in enumerate(args) if i not in exempt_indices}
            if not args_floats.issubset(skill_ctx):
                missing = args_floats - skill_ctx
                reasons.append(f"args_not_in_context:{sorted(missing)}")

    # 14. Bones-features suppression check: same doctrine as the money.py /
    #     weather.py forge validators (server.forge_ops.features_for_entry +
    #     server.suppressions.matches) — a candidate whose bones features
    #     match an active suppression rule for this skill_id is rejected at
    #     intake, so the pool never accumulates entries the runtime would
    #     just mark invalid later.
    features = features_for_entry(candidate)
    if features is None:
        reasons.append("features_unmappable")
    else:
        rule_name = suppressions.matches(skill_id, {"features": features}, suppressions.load_active())
        if rule_name:
            reasons.append(f"suppressed:{rule_name}")

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
    # Track accepted counts per operation to enforce the per-batch quota
    op_counts: dict[str, int] = {}

    for cand in raw_candidates:
        prompt = cand.get("prompt", "")
        operation = cand.get("operation", "")
        combined_existing = existing_prompts | batch_prompts
        reasons = _validate(cand, combined_existing, context_numbers)
        # Per-operation batch cap: at most _MAX_PER_OP_BATCH accepted per
        # `operation` in a single run, so no one op dominates the batch.
        if not reasons and op_counts.get(operation, 0) >= _MAX_PER_OP_BATCH:
            reasons = [f"op_quota:{operation}"]
        if reasons:
            rejected[prompt or repr(cand)] = reasons
        else:
            entry = _make_entry(cand)
            accepted.append(entry)
            batch_prompts.add(prompt.casefold())
            op_counts[operation] = op_counts.get(operation, 0) + 1

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
