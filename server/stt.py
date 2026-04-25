"""Speech-to-text via faster-whisper, running locally.

Model is loaded lazily on first call and cached for the process lifetime.
Default `small.en` (~480MB) is a good speed/accuracy balance for short answers.
"""

import time
from typing import Callable

_MODEL = None
_MODEL_NAME = "small.en"


def _get_model():
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel

        _MODEL = WhisperModel(_MODEL_NAME, device="cpu", compute_type="int8")
    return _MODEL


def warm() -> None:
    """Eagerly load the Whisper model. Called once at server startup."""
    _get_model()


def transcribe(audio_path: str, emit: Callable) -> dict:
    emit("stt.starting", path=audio_path, model=_MODEL_NAME)
    start = time.time()
    model = _get_model()
    segments, info = model.transcribe(
        audio_path,
        language="en",
        beam_size=1,
        vad_filter=False,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    elapsed_ms = int((time.time() - start) * 1000)
    emit(
        "stt.transcribed",
        text=text,
        elapsed_ms=elapsed_ms,
        model=_MODEL_NAME,
    )
    return {"text": text, "elapsed_ms": elapsed_ms}
