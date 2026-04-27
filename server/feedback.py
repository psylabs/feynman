"""Personalized tutor-style feedback for wrong or slow attempts.

Two-stage:
  1. Deterministic `error_facts()` — produces *neutral* observations about
     the relationship between the user's answer and the correct one. No
     interpretation, no labels like "missed_carry". Just the arithmetic
     facts a careful reader could verify.
  2. LLM call — given those facts, the actual numbers, and a compact
     summary of the user's recent attempts on this skill, the model does
     its own interpretation. The system prompt explicitly forbids
     generic platitudes and asks for analysis grounded in the specific
     numbers in front of it.

The split exists because LLMs are bad at arithmetic but good at narrative.
We hand them the math; they hand us the explanation.
"""

import os
import time
from typing import Callable

_CLIENT = None
_MODEL = "gpt-4o-mini"
_SLOW_MULTIPLIER = 1.8


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        _CLIENT = OpenAI()
    return _CLIENT


def should_give_feedback(
    correct: bool,
    skipped: bool,
    latency_ms: int | None,
    target_ms: int | None,
) -> bool:
    if not correct or skipped:
        return True
    if latency_ms and target_ms and latency_ms > _SLOW_MULTIPLIER * target_ms:
        return True
    return False


# ---------------------------------------------------------------------------
# Neutral arithmetic facts about an attempt
# ---------------------------------------------------------------------------


def error_facts(
    parsed: float | None,
    expected: float,
    correct: bool,
    skipped: bool,
) -> dict:
    """Return verifiable observations about the answer-vs-correct relationship.

    Deliberately label-free. No "missed_carry" or "off_by_one" — those are
    interpretations and we leave them to the LLM (or a future empirical
    pattern detector) to derive.
    """
    if skipped:
        return {"status": "skipped"}
    if parsed is None:
        return {"status": "no_clear_answer"}
    if correct:
        return {"status": "correct"}

    diff = parsed - expected
    abs_diff = abs(diff)
    facts: dict = {
        "status": "wrong",
        "your_answer": parsed,
        "correct_answer": expected,
        "signed_error": diff,
        "abs_error": abs_diff,
    }

    if expected:
        facts["error_as_fraction_of_correct"] = round(abs_diff / abs(expected), 3)

    # Digit-level neutral observations (only when both are integer-shaped).
    try:
        if float(parsed).is_integer() and float(expected).is_integer():
            e_str = str(int(abs(expected)))
            p_str = str(int(abs(parsed)))
            facts["digit_count_correct"] = len(e_str)
            facts["digit_count_yours"] = len(p_str)
            facts["last_digit_match"] = e_str[-1] == p_str[-1]
            facts["digit_count_off"] = len(p_str) - len(e_str)
    except (ValueError, TypeError):
        pass

    return facts


# ---------------------------------------------------------------------------
# History summary
# ---------------------------------------------------------------------------


def _summarize_history(attempts: list[dict], current_attempt_id: int | None = None) -> str:
    if not attempts:
        return "(no prior attempts on this skill)"

    lines: list[str] = []
    for a in attempts:
        if current_attempt_id is not None and a.get("id") == current_attempt_id:
            continue
        lat = a.get("resolution_latency_ms")
        prompt = a.get("prompt_text", "")
        if a.get("skipped"):
            verdict = "skipped"
        elif a.get("correct"):
            verdict = f"right · {lat}ms" if lat else "right"
        else:
            verdict = (
                f"WRONG · said {_fmt(a.get('parsed_answer'))}, "
                f"expected {_fmt(a.get('expected_answer'))}"
            )
        lines.append(f"  - {prompt}  →  {verdict}")
        if len(lines) >= 8:
            break

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


_SYSTEM = (
    "You are a sharp, no-fluff mental-math coach speaking to ONE specific user. "
    "Reply with ONE short sentence (max 22 words), addressed to them as 'you'.\n\n"
    "You will receive: the question, their answer, the correct answer, neutral "
    "observations about the numerical relationship between the two, and a small "
    "summary of their recent attempts on this skill. Do your own interpretation "
    "from those numbers — don't pull from a fixed list of bug names.\n\n"
    "Wrong answers: look at THEIR specific number versus the correct one, infer "
    "what plausibly produced it, and say so concretely. If a clear pattern shows "
    "up across recent attempts, name it briefly. If you genuinely can't tell what "
    "went wrong, admit it and offer the strategy you'd use on these numbers.\n\n"
    "Slow but correct: suggest a concrete decomposition shortcut that uses these "
    "exact operands (e.g., 'round 96 up to 100, add 91, take 4 back').\n\n"
    "Forbidden: greetings, praise, formulas in symbolic form, restating the "
    "question, the words 'simply' or 'just', generic encouragement."
)


def generate(
    *,
    prompt: str,
    expected: float,
    parsed: float | None,
    correct: bool,
    skipped: bool,
    latency_ms: int | None,
    target_ms: int,
    skill_id: str,
    parameters: dict | None,
    recent_attempts: list[dict],
    current_attempt_id: int | None,
    emit: Callable,
) -> str | None:
    if not os.environ.get("OPENAI_API_KEY"):
        emit("feedback.skipped", reason="no_api_key")
        return None

    facts = error_facts(parsed, expected, correct, skipped)
    history = _summarize_history(recent_attempts, current_attempt_id)

    if facts["status"] == "skipped":
        situation = f"You skipped this. The correct answer is {_fmt(expected)}."
        ask = "Explain how to compute it on these specific numbers, in one sentence."
    elif facts["status"] == "no_clear_answer":
        situation = (
            f"Your answer wasn't parseable. The correct answer is {_fmt(expected)}."
        )
        ask = "Offer the strategy you'd use on these numbers, in one sentence."
    elif facts["status"] == "wrong":
        situation = (
            f"You answered {_fmt(parsed)}; the correct answer is {_fmt(expected)}."
        )
        ask = (
            "Look at the numerical observations and infer what plausibly "
            "produced their answer; say it specifically in one sentence."
        )
    else:
        situation = (
            f"You got {_fmt(expected)} right but took {latency_ms}ms "
            f"(typical fluent answer is around {target_ms}ms)."
        )
        ask = (
            "Suggest a concrete decomposition shortcut for these specific "
            "operands, in one sentence."
        )

    user_msg = (
        f"Question: {prompt}\n"
        f"Skill: {skill_id}\n"
        f"Problem parameters: {parameters or {}}\n"
        f"{situation}\n"
        f"Numerical observations: {facts}\n\n"
        f"Recent attempts on this skill (most recent first):\n{history}\n\n"
        f"{ask}"
    )

    start = time.time()
    try:
        resp = _client().chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=80,
            temperature=0.4,
        )
        text = (resp.choices[0].message.content or "").strip()
        elapsed_ms = int((time.time() - start) * 1000)
        emit(
            "feedback.generated",
            elapsed_ms=elapsed_ms,
            text=text,
            facts=facts,
            tokens=resp.usage.total_tokens if resp.usage else None,
        )
        return text or None
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        emit("feedback.error", error=str(e), elapsed_ms=elapsed_ms)
        return None


def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f.is_integer():
        return str(int(f))
    return f"{f:g}"
