"""Personalized tutor-style feedback for wrong or slow attempts.

Two-stage:
  1. Deterministic error analysis — characterize the *shape* of the mistake
     (off-by-one, missed carry, swapped operands, etc.) from the actual
     numbers. The LLM is bad at this; we don't trust it to do arithmetic.
  2. LLM call — given the error tags, the actual numbers, and a compact
     summary of the user's recent attempts on this skill, produce one short
     coaching sentence in second person.
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
# Deterministic error analysis
# ---------------------------------------------------------------------------


def analyze_error(
    skill_id: str,
    parameters: dict | None,
    parsed: float | None,
    expected: float,
    correct: bool,
    skipped: bool,
) -> list[str]:
    """Return a list of short tag strings describing the shape of the error."""
    if skipped:
        return ["skipped"]
    if parsed is None:
        return ["no_clear_answer"]
    if correct:
        return ["correct"]

    params = parameters or {}
    diff = parsed - expected
    abs_diff = abs(diff)
    tags: list[str] = []

    if abs_diff == 1:
        tags.append("off_by_one")
    elif abs_diff == 10:
        tags.append("off_by_ten")
    elif 0 < abs_diff <= 5:
        tags.append("close_neighbor")

    a = params.get("a")
    b = params.get("b")

    if skill_id == "addition" and a is not None and b is not None:
        if (a % 10) + (b % 10) >= 10:
            no_carry = ((a // 10) + (b // 10)) * 10 + ((a % 10) + (b % 10) - 10)
            if parsed == no_carry:
                tags.append("missed_carry")

    if skill_id == "subtraction" and a is not None and b is not None:
        if a < b and parsed == b - a:
            tags.append("subtracted_smaller_from_larger")
        if (a % 10) < (b % 10):
            no_borrow = ((a // 10) - (b // 10)) * 10 + abs((a % 10) - (b % 10))
            if parsed == no_borrow:
                tags.append("missed_borrow")

    if skill_id == "multiplication" and a is not None and b is not None:
        if parsed == a + b:
            tags.append("added_instead_of_multiplied")
        for da in (-1, 1):
            if parsed == (a + da) * b:
                tags.append(f"used_{a + da}_times_{b}")
        for db in (-1, 1):
            if parsed == a * (b + db):
                tags.append(f"used_{a}_times_{b + db}")

    if skill_id == "percent_of":
        pct = params.get("percentage")
        base = params.get("base")
        if pct is not None and base is not None:
            if parsed == base / 10 and pct != 10:
                tags.append("computed_10_percent_instead")
            if parsed == base / 100 and pct != 1:
                tags.append("computed_1_percent_instead")
            if pct != 0 and parsed == base * pct / 10:
                tags.append("decimal_off_by_factor_of_10")

    if not tags:
        tags.append("other_error")

    return tags


# ---------------------------------------------------------------------------
# History summary
# ---------------------------------------------------------------------------


def _summarize_history(attempts: list[dict], current_attempt_id: int | None = None) -> str:
    if not attempts:
        return "(no prior attempts on this skill)"

    lines = []
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
            said = a.get("parsed_answer")
            exp = a.get("expected_answer")
            verdict = f"WRONG · said {_fmt(said)}, expected {_fmt(exp)}"
        lines.append(f"  - {prompt}  →  {verdict}")
        if len(lines) >= 8:
            break

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


_SYSTEM = (
    "You are a sharp, no-fluff mental-math coach speaking to ONE specific user. "
    "Reply with ONE short sentence (max 22 words) addressed to them as 'you'. "
    "If they got it wrong, name the SPECIFIC likely mistake using the error tags "
    "and the actual numbers in front of them — never generic platitudes. "
    "If they got it right but slow, suggest a concrete decomposition shortcut "
    "using these exact numbers (e.g. 'round 96 up to 100, add 91, take 4 back'). "
    "If a clear pattern shows up across their recent attempts, briefly note it. "
    "Forbidden: greetings, praise, formulas, restating the question, the words "
    "'simply' or 'just'."
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

    error_tags = analyze_error(skill_id, parameters, parsed, expected, correct, skipped)
    history = _summarize_history(recent_attempts, current_attempt_id)

    if skipped:
        situation = f"You skipped this. The right answer is {_fmt(expected)}."
    elif not correct and parsed is None:
        situation = (
            f"Your answer wasn't parseable. The right answer is {_fmt(expected)}."
        )
    elif not correct:
        situation = (
            f"You answered {_fmt(parsed)}; the right answer is {_fmt(expected)} "
            f"(off by {_fmt(abs((parsed or 0) - expected))})."
        )
    else:
        situation = (
            f"You got {_fmt(expected)} right but took {latency_ms}ms "
            f"(typical fluent answer is around {target_ms}ms)."
        )

    user_msg = (
        f"Question: {prompt}\n"
        f"{situation}\n"
        f"Error tags: {error_tags}\n"
        f"Skill: {skill_id}\n"
        f"Problem parameters: {parameters or {}}\n\n"
        f"Recent attempts on this skill (most recent first):\n{history}"
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
            error_tags=error_tags,
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
