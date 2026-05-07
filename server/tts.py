"""Text-to-speech via OpenAI tts-1, cached on disk by prompt SHA.

Cross-platform replacement for macOS `say`. Cache-keyed by SHA so identical
prompts (e.g. repeated theme reps) skip the API round-trip entirely.
"""

import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

AUDIO_DIR = Path(tempfile.gettempdir()) / "feynman_audio"
AUDIO_DIR.mkdir(exist_ok=True)

_VOICE = "alloy"
_MODEL = "tts-1"
_FORMAT = "wav"
_FALLBACK_TO_SAY = True

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        _CLIENT = OpenAI()
    return _CLIENT


def synthesize(text: str, emit: Callable) -> dict:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    fname = f"tts_{sha}.{_FORMAT}"
    wav = AUDIO_DIR / fname

    cached = wav.exists()
    if not cached:
        start = time.time()
        ok = _render_openai(text, wav, emit)
        if not ok and _FALLBACK_TO_SAY:
            ok = _render_say(text, wav, emit)
        if not ok:
            emit("tts.error", text=text, message="all TTS paths failed")
            return {"filename": fname, "duration_ms": None, "path": str(wav), "cached": False}
        render_ms = int((time.time() - start) * 1000)
    else:
        render_ms = 0

    duration_ms = _audio_duration_ms(wav)
    emit(
        "tts.synthesized",
        text=text,
        duration_ms=duration_ms,
        render_ms=render_ms,
        filename=fname,
        cached=cached,
    )
    return {"filename": fname, "duration_ms": duration_ms, "path": str(wav), "cached": cached}


def _render_openai(text: str, out: Path, emit: Callable) -> bool:
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        with _client().audio.speech.with_streaming_response.create(
            model=_MODEL,
            voice=_VOICE,
            input=text,
            response_format=_FORMAT,
        ) as resp:
            resp.stream_to_file(str(out))
        return True
    except Exception as e:
        emit("tts.openai_error", error=str(e))
        return False


def _render_say(text: str, out: Path, emit: Callable) -> bool:
    """macOS `say` fallback — only fires if OpenAI is unavailable."""
    try:
        subprocess.run(
            ["say", "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@22050", text],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        emit("tts.say_error", error=str(e))
        return False


def _audio_duration_ms(path: Path) -> int | None:
    try:
        out = subprocess.run(
            ["afinfo", str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except Exception:
        return _wav_duration_fallback(path)
    m = re.search(r"estimated duration:\s*([0-9.]+)\s*sec", out)
    if not m:
        return _wav_duration_fallback(path)
    return int(float(m.group(1)) * 1000)


def _wav_duration_fallback(path: Path) -> int | None:
    """Cheap WAV header read so non-macOS hosts still report duration."""
    try:
        import wave

        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 1
            return int((frames / rate) * 1000)
    except Exception:
        return None


def get_audio_path(filename: str) -> Path | None:
    p = AUDIO_DIR / filename
    return p if p.exists() else None
