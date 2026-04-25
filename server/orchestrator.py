"""Session orchestrator — composes the turn lifecycle.

Owns no persistent state; pending-question state is held in memory only between
`next_question` and `submit_answer` calls and keyed by session id.
"""

import uuid

from server import generator, grader, mastery, parser, scheduler, stt, tts
from server.events import EventBus
from server.storage import Storage


class Orchestrator:
    def __init__(self, storage: Storage, bus: EventBus, target_questions: int = 12):
        self.storage = storage
        self.bus = bus
        self.target_questions = target_questions
        self._active: dict[str, dict] = {}  # session_id -> pending question

    def start_session(self) -> dict:
        sid = self.storage.create_session()
        self.bus.emit("session.started", session_id=sid, target=self.target_questions)
        return {"session_id": sid, "target_questions": self.target_questions}

    def next_question(self, sid: str) -> dict:
        states = self.storage.get_all_skill_states()
        choice = scheduler.pick_skill(states, self.bus.emit)
        problem = generator.generate(choice["skill_id"], choice["mastery"])
        self.bus.emit(
            "generator.produced",
            skill_id=choice["skill_id"],
            prompt=problem["prompt"],
            expected=problem["expected"],
            parameters=problem["parameters"],
        )

        audio = tts.synthesize(problem["prompt"], self.bus.emit)

        qid = uuid.uuid4().hex
        position = self.storage.session_attempt_count(sid) + 1
        self._active[sid] = {
            "qid": qid,
            "skill_id": choice["skill_id"],
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
            "skill_id": choice["skill_id"],
            "position": position,
            "target_questions": self.target_questions,
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

        trans = stt.transcribe(audio_path, self.bus.emit)
        parsed = parser.parse(trans["text"])
        self.bus.emit(
            "parser.parsed",
            text=trans["text"],
            value=parsed["value"],
            skipped=parsed["skipped"],
        )

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
            q["skill_id"],
            skill["target_latency_ms"],
            self.bus.emit,
        )

        del self._active[sid]

        return {
            "correct": verdict["correct"],
            "expected": q["expected"],
            "parsed": parsed["value"],
            "skipped": parsed["skipped"],
            "transcript": trans["text"],
            "onset_latency_ms": onset_lat,
            "resolution_latency_ms": resolution_lat,
            "rule": verdict["rule"],
            "position": q["position"],
            "target_questions": self.target_questions,
        }

    def end_session(self, sid: str) -> dict:
        self.storage.end_session(sid)
        self._active.pop(sid, None)
        attempts = self.storage.session_attempts(sid)
        self.bus.emit(
            "session.ended",
            session_id=sid,
            attempt_count=len(attempts),
        )
        return {"session_id": sid, "attempts": attempts}
