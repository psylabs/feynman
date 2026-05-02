"""Session orchestrator — composes the turn lifecycle.

Holds no persistent state; pending-question state lives in memory between
`next_question` and `submit_answer` calls, keyed by session id.

Two session modes:
  - drill: diagnosis-first scheduler identifies the user's slowest facts and
    patterns, then targets those specifically (5 questions).
  - eval: a fixed 20-question plan walks each skill across three difficulty
    levels so we get a real baseline. Bypasses the scheduler.
"""

import uuid

from server import (
    diagnosis,
    eval_plan,
    feedback,
    generator,
    grader,
    mastery,
    parser,
    scheduler,
    session_analysis,
    stt,
    tts,
)
from server.events import EventBus
from server.storage import Storage

DRILL_LENGTH = 12
DRILL_LENGTH_MIN = 3
DRILL_LENGTH_MAX = 30


class Orchestrator:
    def __init__(self, storage: Storage, bus: EventBus):
        self.storage = storage
        self.bus = bus
        self._active: dict[str, dict] = {}
        # Per-session metadata: target length and pre-computed slot plan.
        self._sessions: dict[str, dict] = {}

    # ---- session lifecycle -------------------------------------------------

    def start_session(
        self,
        user_id: str,
        mode: str = "drill",
        target_questions: int | None = None,
    ) -> dict:
        if mode not in ("drill", "eval"):
            raise ValueError(f"invalid mode: {mode}")
        if not self.storage.get_user(user_id):
            raise ValueError(f"unknown user: {user_id}")

        # Make sure this user has a skill_state row for every active skill.
        self.storage.ensure_skill_states_for_user(user_id)

        sid = self.storage.create_session(user_id, mode)
        if mode == "eval":
            target = eval_plan.EVAL_LENGTH
            plan: list[dict] = []
        else:
            if target_questions is None:
                target = DRILL_LENGTH
            else:
                target = max(DRILL_LENGTH_MIN, min(DRILL_LENGTH_MAX, int(target_questions)))
            plan = scheduler.build_session_plan(self.storage, user_id, target, self.bus.emit)

        plan_summary = (
            session_analysis.plan_summary(plan)
            if mode == "drill"
            else None
        )
        self._sessions[sid] = {
            "target": target,
            "plan": plan,
            "original_plan": [slot.copy() for slot in plan],
            "plan_summary": plan_summary,
        }

        self.bus.emit(
            "session.started",
            session_id=sid,
            user_id=user_id,
            mode=mode,
            target=target,
            planned_slots=len(plan),
        )
        return {
            "session_id": sid,
            "mode": mode,
            "target_questions": target,
            "session_plan": plan_summary,
        }

    def end_session(self, sid: str) -> dict:
        session = self.storage.get_session(sid)
        if not session:
            raise ValueError(f"unknown session: {sid}")

        meta = self._sessions.get(sid) or {}
        self.storage.end_session(sid)
        self._active.pop(sid, None)
        self._sessions.pop(sid, None)
        attempts = self.storage.session_attempts(sid)
        self.bus.emit(
            "session.ended",
            session_id=sid,
            mode=session["mode"],
            attempt_count=len(attempts),
        )

        # Compute a diagnosis snapshot for the review screen so the user sees
        # what the system noticed and what the next session will focus on.
        all_attempts = self.storage.all_attempts_for_user(session["user_id"], limit=300)
        fact_stats = diagnosis.compute_fact_stats(all_attempts)
        skill_targets = {}
        for skid in self.storage.all_skill_ids():
            sk = self.storage.get_skill(skid)
            if sk:
                skill_targets[skid] = sk["target_latency_ms"]
        diag = diagnosis.diagnosis_summary(fact_stats, skill_targets, all_attempts)
        analysis = None
        if session["mode"] == "drill" and meta.get("original_plan"):
            analysis = session_analysis.review_analysis(meta["original_plan"], attempts)

        return {
            "session_id": sid,
            "mode": session["mode"],
            "attempts": attempts,
            "diagnosis": diag,
            "session_analysis": analysis,
        }

    # ---- per-turn ----------------------------------------------------------

    def next_question(self, sid: str) -> dict:
        session = self.storage.get_session(sid)
        if not session:
            raise ValueError(f"unknown session: {sid}")
        user_id = session["user_id"]
        mode = session["mode"]
        position = self.storage.session_attempt_count(sid) + 1
        meta = self._sessions.get(sid) or {}
        target = meta.get("target") or (
            eval_plan.EVAL_LENGTH if mode == "eval" else DRILL_LENGTH
        )

        if mode == "eval":
            skill_id, level = eval_plan.step(position)
            self.bus.emit(
                "eval.step",
                position=position,
                skill_id=skill_id,
                level=level,
            )
            problem = generator.generate(skill_id, level=level)
        else:
            plan = meta.get("plan") or []
            if plan:
                pick = plan.pop(0)
                self.bus.emit(
                    "scheduler.plan_pick",
                    fact_key=pick.get("fact_key"),
                    role=pick.get("role"),
                    remaining=len(plan),
                )
            else:
                pick = scheduler.pick_drill(self.storage, user_id, self.bus.emit)
            skill_id = pick["skill_id"]
            level = pick["level"]
            problem = generator.generate(
                skill_id,
                level=level,
                target=pick.get("target_fact"),
            )

        self.bus.emit(
            "generator.produced",
            skill_id=skill_id,
            prompt=problem["prompt"],
            expected=problem["expected"],
            parameters=problem["parameters"],
        )

        audio = tts.synthesize(problem["prompt"], self.bus.emit)

        qid = uuid.uuid4().hex
        self._active[sid] = {
            "qid": qid,
            "user_id": user_id,
            "mode": mode,
            "skill_id": skill_id,
            "prompt": problem["prompt"],
            "expected": problem["expected"],
            "parameters": problem["parameters"],
            "audio_duration_ms": audio["duration_ms"],
            "position": position,
        }

        return {
            "qid": qid,
            "prompt_text": problem["prompt"],
            "audio_url": f"/audio/{audio['filename']}",
            "audio_duration_ms": audio["duration_ms"],
            "skill_id": skill_id,
            "position": position,
            "target_questions": target,
            "mode": mode,
        }

    def submit_answer(
        self,
        sid: str,
        qid: str,
        audio_path: str,
        prompt_end_ts: float,
        onset_ts: float,
        resolution_ts: float,
    ) -> dict:
        q = self._active.get(sid)
        if not q or q["qid"] != qid:
            raise ValueError("no active question for this session")

        skill = self.storage.get_skill(q["skill_id"])
        if not skill:
            raise ValueError(f"skill not found: {q['skill_id']}")

        meta = self._sessions.get(sid) or {}
        target = meta.get("target") or (
            eval_plan.EVAL_LENGTH if q["mode"] == "eval" else DRILL_LENGTH
        )

        trans = stt.transcribe(audio_path, self.bus.emit)
        text = (trans.get("text") or "").strip()

        if len(text) < 1:
            self.bus.emit(
                "answer.no_audio",
                session_id=sid,
                qid=qid,
                stt_error=trans.get("error"),
            )
            return {
                "audio_failed": True,
                "message": "Didn't catch that — try again.",
                "transcript": text,
                "position": q["position"],
                "target_questions": target,
            }

        parsed = parser.parse(text)
        self.bus.emit(
            "parser.parsed",
            text=text,
            value=parsed["value"],
            skipped=parsed["skipped"],
        )

        if parsed["value"] is None and not parsed["skipped"]:
            self.bus.emit(
                "answer.unparseable",
                session_id=sid,
                qid=qid,
                transcript=text,
            )
            return {
                "audio_failed": True,
                "message": f"Couldn't parse a number from “{text}” — try again.",
                "transcript": text,
                "position": q["position"],
                "target_questions": target,
            }

        if parsed["skipped"]:
            verdict = {"correct": False, "error_magnitude": None, "rule": "skipped"}
        else:
            verdict = grader.grade(parsed["value"], q["expected"], skill["tolerance"])
        self.bus.emit(
            "grader.verdict",
            expected=q["expected"],
            parsed=parsed["value"],
            **verdict,
        )

        onset_lat = (
            int((onset_ts - prompt_end_ts) * 1000)
            if onset_ts and prompt_end_ts
            else None
        )
        resolution_lat = (
            int((resolution_ts - prompt_end_ts) * 1000)
            if resolution_ts and prompt_end_ts
            else None
        )

        attempt = {
            "session_id": sid,
            "skill_id": q["skill_id"],
            "position_in_session": q["position"],
            "prompt_text": q["prompt"],
            "prompt_audio_ms": q["audio_duration_ms"],
            "prompt_end_ts": prompt_end_ts,
            "onset_ts": onset_ts,
            "resolution_ts": resolution_ts,
            "onset_latency_ms": onset_lat,
            "resolution_latency_ms": resolution_lat,
            "raw_transcript": trans["text"],
            "parsed_answer": parsed["value"],
            "expected_answer": q["expected"],
            "correct": verdict["correct"],
            "error_magnitude": verdict["error_magnitude"],
            "skipped": parsed["skipped"],
            "parameters": q["parameters"],
        }
        attempt_id = self.storage.insert_attempt(attempt)
        self.bus.emit(
            "attempt.recorded",
            attempt_id=attempt_id,
            skill_id=q["skill_id"],
            correct=verdict["correct"],
            skipped=parsed["skipped"],
            resolution_latency_ms=resolution_lat,
        )

        mastery.update(
            self.storage,
            q["user_id"],
            q["skill_id"],
            skill["target_latency_ms"],
            self.bus.emit,
        )

        # Mid-drill coaching is intentionally disabled for now. The feedback
        # module stays available for a later post-session/stubborn-pattern design.
        wants_feedback = False

        del self._active[sid]

        return {
            "attempt_id": attempt_id,
            "correct": verdict["correct"],
            "expected": q["expected"],
            "parsed": parsed["value"],
            "skipped": parsed["skipped"],
            "transcript": trans["text"],
            "onset_latency_ms": onset_lat,
            "resolution_latency_ms": resolution_lat,
            "rule": verdict["rule"],
            "feedback_pending": wants_feedback,
            "position": q["position"],
            "target_questions": target,
            "mode": q["mode"],
        }

    # ---- async feedback ----------------------------------------------------

    def generate_feedback_for(self, attempt_id: int) -> None:
        attempt = self.storage.get_attempt(attempt_id)
        if not attempt:
            return
        skill = self.storage.get_skill(attempt["skill_id"])
        if not skill:
            return
        user_id = attempt.get("user_id")
        if not user_id:
            return

        params = attempt.get("parameters") or {}
        if isinstance(params, str):
            try:
                import json as _json

                params = _json.loads(params)
            except (TypeError, ValueError):
                params = {}

        recent = self.storage.recent_attempts_for_skill(
            user_id, attempt["skill_id"], limit=10
        )

        fb_text = feedback.generate(
            prompt=attempt["prompt_text"],
            expected=attempt["expected_answer"],
            parsed=attempt.get("parsed_answer"),
            correct=bool(attempt.get("correct")),
            skipped=bool(attempt.get("skipped")),
            latency_ms=attempt.get("resolution_latency_ms"),
            target_ms=skill["target_latency_ms"],
            skill_id=attempt["skill_id"],
            parameters=params,
            recent_attempts=recent,
            current_attempt_id=attempt_id,
            emit=self.bus.emit,
        )
        if fb_text:
            self.storage.update_attempt_notes(attempt_id, fb_text)
            self.bus.emit(
                "feedback.ready", attempt_id=attempt_id, text=fb_text
            )
