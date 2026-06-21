"""Speech-to-text via OpenAI Whisper API.

Much faster than local for short answers (~1s round-trip vs 5–15s on CPU).
The `prompt` parameter biases the model toward number transcription, which
helps with the "two → to" / "four → for" homophone problem.
"""

import re
import time
from typing import Callable

_CLIENT = None
_MODEL = "gpt-4o-mini-transcribe"
_PROMPT_HINT = (
    "Transcribe spoken numbers as digits. "
    "Output 47 not forty-seven, 123 not one twenty-three, 18.5 not eighteen point five. "
    "Output a single short numeric answer, or the word 'skip' if no number was spoken."
)
# Probe 3: identical to _PROMPT_HINT but with the "skip" escape hatch removed,
# so we can A/B whether the skip clause is causing false skips on the *same*
# clip. Used only as a shadow re-transcribe when the first pass returns "skip".
_PROBE_PROMPT = (
    "Transcribe spoken numbers as digits. "
    "Output 47 not forty-seven, 123 not one twenty-three, 18.5 not eighteen point five. "
    "Output a single short numeric answer."
)
_SKIP_RE = re.compile(r"\bskip\b", re.IGNORECASE)


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        _CLIENT = OpenAI()
    return _CLIENT


def warm() -> None:
    """No-op placeholder kept for compatibility with the local-model path."""
    return None


def _transcribe_once(audio_path: str, prompt: str) -> str:
    with open(audio_path, "rb") as f:
        response = _client().audio.transcriptions.create(
            model=_MODEL,
            file=f,
            language="en",
            prompt=prompt,
        )
    return (response.text or "").strip()


def _looks_numeric(text: str) -> bool:
    return re.search(r"\d", text) is not None


def transcribe(audio_path: str, emit: Callable) -> dict:
    emit("stt.starting", path=audio_path, model=_MODEL)
    start = time.time()
    try:
        text = _transcribe_once(audio_path, _PROMPT_HINT)
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        emit("stt.error", error=str(e), elapsed_ms=elapsed_ms)
        return {"text": "", "elapsed_ms": elapsed_ms, "error": str(e)}

    elapsed_ms = int((time.time() - start) * 1000)
    emit("stt.transcribed", text=text, elapsed_ms=elapsed_ms, model=_MODEL)

    # Probe 3 (diagnostic only): if the first pass skipped, re-transcribe the
    # same clip without the "skip" clause to see what it would have produced.
    # Behavior is unchanged — we still return the original `text`.
    if _SKIP_RE.search(text):
        try:
            alt = _transcribe_once(audio_path, _PROBE_PROMPT)
            emit(
                "stt.skip_probe",
                primary=text,
                alt=alt,
                alt_numeric=_looks_numeric(alt),
                verdict="prompt_false_skip" if _looks_numeric(alt) else "audio_unclear",
            )
        except Exception as e:
            emit("stt.skip_probe_error", error=str(e))

    return {"text": text, "elapsed_ms": elapsed_ms}
