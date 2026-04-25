"""Speech-to-text via OpenAI Whisper API.

Much faster than local for short answers (~1s round-trip vs 5–15s on CPU).
The `prompt` parameter biases the model toward number transcription, which
helps with the "two → to" / "four → for" homophone problem.
"""

import time
from typing import Callable

_CLIENT = None
_MODEL = "gpt-4o-mini-transcribe"
_PROMPT_HINT = (
    "Transcribe spoken numbers as digits. "
    "Output 47 not forty-seven, 123 not one twenty-three, 18.5 not eighteen point five. "
    "Output a single short numeric answer, or the word 'skip' if no number was spoken."
)


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        _CLIENT = OpenAI()
    return _CLIENT


def warm() -> None:
    """No-op placeholder kept for compatibility with the local-model path."""
    return None


def transcribe(audio_path: str, emit: Callable) -> dict:
    emit("stt.starting", path=audio_path, model=_MODEL)
    start = time.time()
    try:
        with open(audio_path, "rb") as f:
            response = _client().audio.transcriptions.create(
                model=_MODEL,
                file=f,
                language="en",
                prompt=_PROMPT_HINT,
            )
        text = (response.text or "").strip()
    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        emit("stt.error", error=str(e), elapsed_ms=elapsed_ms)
        return {"text": "", "elapsed_ms": elapsed_ms, "error": str(e)}

    elapsed_ms = int((time.time() - start) * 1000)
    emit("stt.transcribed", text=text, elapsed_ms=elapsed_ms, model=_MODEL)
    return {"text": text, "elapsed_ms": elapsed_ms}
