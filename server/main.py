"""FastAPI app: serves the static frontend and the small JSON+SSE API."""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load .env (if present) before any module reads OPENAI_API_KEY.
load_dotenv(Path(__file__).parent.parent / ".env")

from server import tts  # noqa: E402
from server.events import EventBus  # noqa: E402
from server.orchestrator import Orchestrator  # noqa: E402
from server.storage import Storage  # noqa: E402

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
ANSWER_DIR = Path(tempfile.gettempdir()) / "feynman_answers"
ANSWER_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Feynman")
bus = EventBus(LOG_DIR)
storage = Storage(DATA_DIR / "feynman.db")

# Load skills from YAML on startup, pruning any rows for skills no longer defined.
with (ROOT / "skills.yaml").open() as f:
    skill_defs = yaml.safe_load(f) or []
keep_ids = [s["id"] for s in skill_defs]
removed = storage.prune_skills_not_in(keep_ids)
if removed:
    print(f"[startup] pruned obsolete skills: {removed}", flush=True)
    bus.emit("startup.pruned_skills", removed=removed)
for skill in skill_defs:
    storage.upsert_skill(skill)
    storage.init_skill_state(skill["id"])

orch = Orchestrator(storage, bus)


@app.on_event("startup")
async def check_env():
    if not os.environ.get("OPENAI_API_KEY"):
        bus.emit(
            "startup.warning",
            message="OPENAI_API_KEY is not set — STT and feedback will fail.",
        )
        print(
            "[startup] WARNING: OPENAI_API_KEY not set. "
            "Set it in the environment or create a .env file at the repo root.",
            flush=True,
        )
    else:
        bus.emit("startup.ready")
        print("[startup] ready (OpenAI key detected)", flush=True)


@app.post("/session/start")
def session_start():
    return orch.start_session()


@app.post("/session/next")
async def session_next(payload: dict):
    sid = payload.get("session_id")
    if not sid:
        raise HTTPException(400, "session_id required")
    return await asyncio.to_thread(orch.next_question, sid)


@app.post("/session/submit")
async def session_submit(
    session_id: str = Form(...),
    qid: str = Form(...),
    prompt_end_ts: float = Form(...),
    onset_ts: float = Form(...),
    resolution_ts: float = Form(...),
    audio: UploadFile = File(...),
):
    audio_bytes = await audio.read()
    suffix = Path(audio.filename or "answer.webm").suffix or ".webm"
    audio_path = ANSWER_DIR / f"answer_{uuid.uuid4().hex}{suffix}"
    audio_path.write_bytes(audio_bytes)
    bus.emit(
        "answer.received",
        session_id=session_id,
        qid=qid,
        bytes=len(audio_bytes),
        path=str(audio_path),
    )
    try:
        return await asyncio.to_thread(
            orch.submit_answer,
            session_id,
            qid,
            str(audio_path),
            prompt_end_ts,
            onset_ts,
            resolution_ts,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/session/end")
def session_end(payload: dict):
    sid = payload.get("session_id")
    if not sid:
        raise HTTPException(400, "session_id required")
    return orch.end_session(sid)


@app.get("/audio/{name}")
def audio(name: str):
    p = tts.get_audio_path(name)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/wav")


@app.get("/events")
async def events():
    queue = bus.subscribe()

    async def stream():
        try:
            yield f"data: {json.dumps({'ts': 0, 'type': 'sse.connected'})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Static frontend mounted last so API routes take precedence
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="static")


def main():
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
