"""FastAPI app: serves the static frontend and the small JSON+SSE API."""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load .env (if present) before any module reads OPENAI_API_KEY.
load_dotenv(Path(__file__).parent.parent / ".env")

from server import diagnosis, tts  # noqa: E402
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

# Make sure every existing user has a skill_state row for every active skill.
for u in storage.list_users():
    storage.ensure_skill_states_for_user(u["id"])

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


@app.get("/users")
def users_list():
    out = []
    for u in storage.list_users():
        out.append({
            "id": u["id"],
            "name": u["name"],
            "has_completed_eval": storage.has_completed_eval(u["id"]),
        })
    return out


@app.post("/users")
def users_create(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    try:
        u = storage.create_user(name)
    except Exception as e:
        raise HTTPException(400, str(e))
    storage.ensure_skill_states_for_user(u["id"])
    bus.emit("user.created", user_id=u["id"], name=name)
    return {"id": u["id"], "name": u["name"], "has_completed_eval": False}


@app.get("/profile/{user_id}")
def profile(user_id: str):
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(404, "user not found")

    attempts = storage.all_attempts_for_user(user_id, limit=500)
    fact_stats = diagnosis.compute_fact_stats(attempts)

    # Build target latency map
    skill_targets = {}
    for sid in storage.all_skill_ids():
        skill = storage.get_skill(sid)
        if skill:
            skill_targets[sid] = skill["target_latency_ms"]

    return {
        "user": {"id": user["id"], "name": user["name"]},
        "skills": storage.per_skill_stats(user_id),
        "has_completed_eval": storage.has_completed_eval(user_id),
        "slowest_facts": diagnosis.slowest_facts(fact_stats, min_attempts=2, limit=15),
        "worst_accuracy": diagnosis.worst_accuracy_facts(fact_stats, min_attempts=2, limit=10),
        "regressions": diagnosis.recent_regressions(attempts),
        "next_drills": diagnosis.drill_priorities(fact_stats, skill_targets, min_attempts=2, limit=10),
        "factor_families": diagnosis.factor_family_stats(fact_stats),
    }


@app.get("/diagnosis/{user_id}")
def diagnosis_preview(user_id: str):
    """Compact diagnosis used by the start-screen teaser."""
    if not storage.get_user(user_id):
        raise HTTPException(404, "user not found")
    attempts = storage.all_attempts_for_user(user_id, limit=300)
    fact_stats = diagnosis.compute_fact_stats(attempts)
    skill_targets = {}
    for sid in storage.all_skill_ids():
        skill = storage.get_skill(sid)
        if skill:
            skill_targets[sid] = skill["target_latency_ms"]
    summary = diagnosis.diagnosis_summary(fact_stats, skill_targets, attempts)
    return {**summary, **storage.home_stats(user_id)}


@app.get("/leaderboard")
def leaderboard():
    return {
        "users": storage.leaderboard_data(),
        "skills": [
            {"id": sid, "display_name": (storage.get_skill(sid) or {}).get("display_name", sid)}
            for sid in storage.all_skill_ids()
        ],
    }


@app.post("/session/start")
def session_start(payload: dict):
    user_id = payload.get("user_id")
    mode = payload.get("mode", "drill")
    target_questions = payload.get("target_questions")
    if not user_id:
        raise HTTPException(400, "user_id required")
    try:
        return orch.start_session(user_id, mode, target_questions=target_questions)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/session/next")
async def session_next(payload: dict):
    sid = payload.get("session_id")
    if not sid:
        raise HTTPException(400, "session_id required")
    return await asyncio.to_thread(orch.next_question, sid)


@app.post("/session/peek")
async def session_peek(payload: dict):
    """Pre-generate the next question so the next /session/next is instant.

    Safe to call after /session/submit returns; the response is exactly what
    /session/next would have returned. Returns {"peeked": false} when the
    session has no question to pre-generate (ended, target reached, or a
    question is already pending).
    """
    sid = payload.get("session_id")
    if not sid:
        raise HTTPException(400, "session_id required")
    result = await asyncio.to_thread(orch.peek_next_question, sid)
    if result is None:
        return {"peeked": False}
    return {"peeked": True, **result}


@app.post("/session/submit")
async def session_submit(
    background_tasks: BackgroundTasks,
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
        result = await asyncio.to_thread(
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

    # If this attempt was recorded and warrants feedback, generate it in the
    # background so the response returns immediately. Feedback streams to the
    # browser via the SSE event bus when ready.
    if result.get("attempt_id") and result.get("feedback_pending"):
        background_tasks.add_task(
            asyncio.to_thread, orch.generate_feedback_for, result["attempt_id"]
        )

    return result


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


class NoCacheStaticFiles(StaticFiles):
    """Static files with Cache-Control: no-store. Useful during MVP iteration
    so the browser never serves a stale app.js or index.html after a deploy."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        return response


# Static frontend mounted last so API routes take precedence
app.mount("/", NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="static")


def main():
    import uvicorn

    host = os.environ.get("FEYNMAN_HOST", "0.0.0.0")
    port = int(os.environ.get("FEYNMAN_PORT", "8765"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
