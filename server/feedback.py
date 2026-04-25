"""Brief tutor-style feedback for wrong or slow attempts.

Called by the orchestrator after grading. Returns a single short sentence,
or None if no feedback is warranted (or the LLM call fails).
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


def generate(
    prompt: str,
    expected: float,
    parsed: float | None,
    correct: bool,
    skipped: bool,
    latency_ms: int | None,
    target_ms: int,
    emit: Callable,
) -> str | None:
    if not os.environ.get("OPENAI_API_KEY"):
        emit("feedback.skipped", reason="no_api_key")
        return None

    if skipped:
        situation = (
            f"They skipped. The correct answer is {_fmt(expected)}."
        )
        instruction = "Explain how to compute it in one short sentence."
    elif not correct:
        if parsed is None:
            situation = (
                f"Their answer wasn't parseable. The correct answer is {_fmt(expected)}."
            )
            instruction = "Explain how to compute it in one short sentence."
        else:
            situation = (
                f"They answered {_fmt(parsed)}; the correct answer is {_fmt(expected)}."
            )
            instruction = "In one short sentence, name what they likely missed."
    else:
        situation = (
            f"They got {_fmt(expected)} correct in {latency_ms} ms "
            f"(typical fluent answer is around {target_ms} ms)."
        )
        instruction = "In one short sentence, suggest a faster mental shortcut."

    system = (
        "You are a no-frills mental-math coach. Reply with ONE short sentence "
        "(max 20 words). Conversational and direct. No greetings, no praise, "
        "no formulas, no restating the question. Just the tip."
    )
    user = f"Question: {prompt}\n{situation}\n{instruction}"

    start = time.time()
    try:
        resp = _client().chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=80,
            temperature=0.5,
        )
        text = (resp.choices[0].message.content or "").strip()
        elapsed_ms = int((time.time() - start) * 1000)
        emit(
            "feedback.generated",
            elapsed_ms=elapsed_ms,
            text=text,
            tokens=resp.usage.total_tokens if resp.usage else None,
        )
        return text or None
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        emit("feedback.error", error=str(e), elapsed_ms=elapsed_ms)
        return None


def _fmt(v: float) -> str:
    if v is None:
        return "—"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:g}"
