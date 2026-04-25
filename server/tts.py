"""Text-to-speech via macOS `say` + `afconvert` to WAV (browser-friendly)."""

import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable

AUDIO_DIR = Path(tempfile.gettempdir()) / "feynman_audio"
AUDIO_DIR.mkdir(exist_ok=True)


def synthesize(text: str, emit: Callable) -> dict:
    fname = f"tts_{uuid.uuid4().hex}.wav"
    wav = AUDIO_DIR / fname
    aiff = wav.with_suffix(".aiff")

    start = time.time()
    subprocess.run(
        ["say", "-o", str(aiff), text],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["afconvert", str(aiff), str(wav), "-d", "LEI16@22050", "-f", "WAVE"],
        check=True,
        capture_output=True,
    )
    aiff.unlink(missing_ok=True)
    render_ms = int((time.time() - start) * 1000)

    duration_ms = _audio_duration_ms(wav)
    emit(
        "tts.synthesized",
        text=text,
        duration_ms=duration_ms,
        render_ms=render_ms,
        filename=fname,
    )
    return {"filename": fname, "duration_ms": duration_ms, "path": str(wav)}


def _audio_duration_ms(path: Path) -> int | None:
    try:
        out = subprocess.run(
            ["afinfo", str(path)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except Exception:
        return None
    m = re.search(r"estimated duration:\s*([0-9.]+)\s*sec", out)
    if not m:
        return None
    return int(float(m.group(1)) * 1000)


def get_audio_path(filename: str) -> Path | None:
    p = AUDIO_DIR / filename
    return p if p.exists() else None
