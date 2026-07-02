"""Session orchestrator — composes the turn lifecycle.

Holds no persistent state; pending-question state lives in memory between
`next_question` and `submit_answer` calls, keyed by session id.

Two session modes:
  - drill: diagnosis-first scheduler identifies the user's slowest facts and
    patterns, then targets those specifically (5 questions).
  - eval: a fixed 20-question plan walks each skill across three difficulty
    levels so we get a real baseline. Bypasses the scheduler.
"""

import logging
import uuid
from datetime import datetime, timezone

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
    srs,
    stt,
    taxonomy,
    tts,
)
from server.events import EventBus
from server.storage import Storage

_log = logging.getLogger(__name__)

DRILL_LENGTH = 12
DRILL_LENGTH_MIN = 3
DRILL_LENGTH_MAX = 30


def _to_float(v):
    """Coerce JSON-supplied answers/expected values to float, or None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class Orchestrator:
    """Composes the turn lifecycle: scheduler -> generator -> TTS -> STT ->
    parser -> grader -> storage -> mastery.

    See ``docs/architecture.md`` for the sequence diagram with file:line
    refs. Three entry points correspond to the three HTTP routes in
    ``server/main.py``:

    - ``start_session`` (``/session/start``) creates the session row and, for
      drill mode, computes a slot plan from diagnosis.
    - ``next_question`` (``/session/next``) picks a skill, generates a
      prompt, synthesises TTS, and returns ``{qid, prompt_text, audio_url}``.
    - ``submit_answer`` (``/session/submit``) transcribes the audio, parses
      a number, grades it, writes the attempt row, and recomputes mastery.

    Pending-question state lives only in ``self._active`` between
    ``next_question`` and ``submit_answer`` calls; everything durable goes
    through ``Storage``.
    """

    def __init__(self, storage: Storage, bus: EventBus):
        self.storage = storage
        self.bus = bus
        self._active: dict[str, dict] = {}
        # Per-session metadata: target length and pre-computed slot plan.
        self._sessions: dict[str, dict] = {}

    # ---- SRS hook ----------------------------------------------------------

    def _record_srs(
        self,
        user_id: str,
        skill_id: str,
        parameters: dict,
        correct: bool,
        onset_ms: int | None,
        resolution_ms: int | None,
        target_ms: int,
        item_key_override: str | None = None,
        when: datetime | None = None,
    ) -> None:
        """Grade one attempt into item_state via FSRS.

        Classifies the attempt, picks the right latency by tier (onset for
        primitives, resolution for compounds), rates it, and upserts the card.
        Silently returns when classify() returns None or on any error.
        Skipped attempts must not reach this method — callers are responsible.

        ``when`` is the review timestamp fed to FSRS (defaults to now). The
        offline bulk-sync path passes each attempt's own ``created_at`` so
        historical sessions schedule from when they actually happened.
        """
        try:
            taxon = taxonomy.classify(skill_id, parameters)
            if taxon is None:
                return

            if taxon.tier == "primitive":
                latency = onset_ms
                item_target_ms = taxonomy.PRIMITIVE_ONSET_TARGET_MS
            else:
                latency = resolution_ms
                item_target_ms = target_ms

            rating = srs.rate(correct, latency, item_target_ms)
            now = when or datetime.now(timezone.utc)

            existing = self.storage.get_item_state(user_id, taxon.key)
            card_json = existing["card_json"] if existing else None
            new_card_json, due_at = srs.review(card_json, rating, now)

            self.storage.upsert_item_state(
                user_id, taxon.key, taxon.tier, taxon.family,
                new_card_json, due_at, int(rating),
            )

            if item_key_override is not None:
                override_existing = self.storage.get_item_state(user_id, item_key_override)
                override_card_json = override_existing["card_json"] if override_existing else None
                override_new_card_json, override_due_at = srs.review(
                    override_card_json, rating, now,
                )
                override_family = (
                    item_key_override.rsplit(":", 1)[0]
                    if ":" in item_key_override
                    else item_key_override
                )
                self.storage.upsert_item_state(
                    user_id, item_key_override, "compound", override_family,
                    override_new_card_json, override_due_at, int(rating),
                )
        except Exception:
            _log.warning(
                "SRS hook failed for user=%s skill=%s", user_id, skill_id,
                exc_info=True,
            )

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
        elif session["mode"] == "drill":
            analysis = session_analysis.exploratory_review_analysis(
                attempts,
                fact_stats,
                skill_targets,
            )

        if analysis is not None:
            analysis["patterns"] = session_analysis.pattern_analysis(all_attempts)

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

        meta = self._sessions.setdefault(sid, {})
        peek = meta.pop("peek", None)
        if peek:
            self._active[sid] = peek["active_state"]
            self.bus.emit("orchestrator.peek_consumed", session_id=sid)
            return peek["payload"]

        produced = self._produce_question(sid, session)
        self._active[sid] = produced["active_state"]
        return produced["payload"]

    def peek_next_question(self, sid: str) -> dict | None:
        """Pre-generate the next question and stash it. Returns the same
        payload `next_question` would return; safe to call repeatedly. No-op
        when there's still an active question pending submission, when the
        session has ended, or when the session has already hit its target.
        """
        session = self.storage.get_session(sid)
        if not session or session.get("ended_at"):
            return None
        if sid in self._active:
            return None
        if sid not in self._sessions:
            return None
        meta = self._sessions[sid]
        target = meta.get("target") or 0
        position = self.storage.session_attempt_count(sid) + 1
        if target and position > target:
            return None
        if meta.get("peek"):
            return meta["peek"]["payload"]
        produced = self._produce_question(sid, session, peek=True)
        meta["peek"] = produced
        self.bus.emit("orchestrator.peek_built", session_id=sid)
        return produced["payload"]

    def _produce_question(self, sid: str, session: dict, peek: bool = False) -> dict:
        user_id = session["user_id"]
        mode = session["mode"]
        position = self.storage.session_attempt_count(sid) + 1
        meta = self._sessions.get(sid) or {}
        target = meta.get("target") or (
            eval_plan.EVAL_LENGTH if mode == "eval" else DRILL_LENGTH
        )
        covers = None  # skeleton item_key a skinned plan slot also credits

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
                    covers=pick.get("covers"),
                    remaining=len(plan),
                )
            else:
                pick = scheduler.pick_drill(self.storage, user_id, self.bus.emit)
            skill_id = pick["skill_id"]
            level = pick["level"]
            # A skinned grounded slot credits the underlying skeleton item too.
            covers = pick.get("covers")
            if covers is not None and not isinstance(covers, str):
                covers = None
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
            peek=peek,
        )

        audio = tts.synthesize(problem["prompt"], self.bus.emit)

        qid = uuid.uuid4().hex
        active_state = {
            "qid": qid,
            "user_id": user_id,
            "mode": mode,
            "skill_id": skill_id,
            "prompt": problem["prompt"],
            "expected": problem["expected"],
            "parameters": problem["parameters"],
            "audio_duration_ms": audio["duration_ms"],
            "position": position,
            "covers": covers,
        }
        payload = {
            "qid": qid,
            "prompt_text": problem["prompt"],
            "audio_url": f"/audio/{audio['filename']}",
            "audio_duration_ms": audio["duration_ms"],
            "skill_id": skill_id,
            "position": position,
            "target_questions": target,
            "mode": mode,
        }
        return {"active_state": active_state, "payload": payload}

    def record_manual_skip(
        self,
        sid: str,
        qid: str,
        prompt_end_ts: float,
        onset_ts: float,
        resolution_ts: float,
        client_meta: dict | None = None,
    ) -> dict:
        """Record an intentional skip from the Skip button (tagged distinctly
        from STT-heard skips so logs can tell them apart)."""
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
        self.bus.emit(
            "answer.manual_skip", session_id=sid, qid=qid,
            client_meta=client_meta or {},
        )
        resolution_lat = (
            int((resolution_ts - prompt_end_ts) * 1000)
            if resolution_ts and prompt_end_ts else None
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
            "onset_latency_ms": None,
            "resolution_latency_ms": resolution_lat,
            "raw_transcript": None,
            "parsed_answer": None,
            "answer_mode": "manual_skip",
            "expected_answer": q["expected"],
            "correct": False,
            "error_magnitude": None,
            "skipped": True,
            "parameters": q["parameters"],
        }
        attempt_id = self.storage.insert_attempt(attempt)
        self.bus.emit(
            "attempt.recorded", attempt_id=attempt_id, skill_id=q["skill_id"],
            correct=False, skipped=True, resolution_latency_ms=resolution_lat,
        )
        mastery.update(
            self.storage, q["user_id"], q["skill_id"],
            skill["target_latency_ms"], self.bus.emit,
        )
        del self._active[sid]
        return {
            "attempt_id": attempt_id,
            "correct": False,
            "expected": q["expected"],
            "parsed": None,
            "skipped": True,
            "transcript": None,
            "onset_latency_ms": None,
            "resolution_latency_ms": resolution_lat,
            "rule": "skipped",
            "feedback_pending": False,
            "position": q["position"],
            "target_questions": target,
            "mode": q["mode"],
            "manual_skip": True,
        }

    def submit_answer(
        self,
        sid: str,
        qid: str,
        audio_path: str,
        prompt_end_ts: float,
        onset_ts: float,
        resolution_ts: float,
        client_meta: dict | None = None,
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
                client_meta=client_meta or {},
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
                client_meta=client_meta or {},
            )
            return {
                "audio_failed": True,
                "message": f"Couldn't parse a number from “{text}” — try again.",
                "transcript": text,
                "position": q["position"],
                "target_questions": target,
            }

        if parsed["skipped"]:
            # STT transcribed "skip". With a dedicated Skip button now handling
            # intentional skips, a heard "skip" is almost always a misrecognition
            # of missing/unclear audio (see stt.skip_probe), so route it to the
            # retry path instead of silently recording a skip.
            self.bus.emit(
                "answer.stt_skip_retry",
                session_id=sid, qid=qid, transcript=text,
                client_meta=client_meta or {},
            )
            return {
                "audio_failed": True,
                "message": "Didn't catch that — try again. (Tap Skip to skip.)",
                "transcript": text,
                "position": q["position"],
                "target_questions": target,
            }

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
            "answer_mode": "voice",
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

        # Grade into FSRS item_state (skipped attempts are handled above via
        # audio_failed returns, so every attempt reaching here is a real grade).
        self._record_srs(
            user_id=q["user_id"],
            skill_id=q["skill_id"],
            parameters=q["parameters"],
            correct=verdict["correct"],
            onset_ms=onset_lat,
            resolution_ms=resolution_lat,
            target_ms=skill["target_latency_ms"],
            item_key_override=q.get("covers"),
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

    # ---- offline sync ------------------------------------------------------

    def record_bulk_attempts(self, user_id: str, attempts: list) -> dict:
        """Flush a batch of attempts captured offline by the mobile app.

        The server re-grades each attempt (it is the source of truth for
        correctness and mastery) and recomputes mastery once per touched skill.
        Each attempt carries a numeric ``parsed_answer`` (typed/parsed
        on-device) or ``skipped``; audio-only offline answers are out of scope.
        All attempts in a batch are grouped under one synthetic drill session.

        Non-skipped attempts are also graded into FSRS using the attempt's own
        ``created_at`` timestamp (not now), so offline sessions advance the
        schedule from when they actually happened. Skeleton ``covers`` credit is
        online-only — the sync payload carries no covers field.
        """
        sid = self.storage.create_session(user_id, "drill")
        recorded = []
        skills_touched: dict[str, int] = {}

        for i, a in enumerate(attempts, start=1):
            skill_id = a.get("skill_id")
            skill = self.storage.get_skill(skill_id) if skill_id else None
            expected = _to_float(a.get("expected_answer", a.get("expected")))
            if not skill or expected is None:
                recorded.append({"skill_id": skill_id, "recorded": False, "reason": "unknown_skill_or_expected"})
                continue

            skipped = bool(a.get("skipped"))
            parsed_val = _to_float(a.get("parsed_answer"))
            if skipped:
                verdict = {"correct": False, "error_magnitude": None, "rule": "skipped"}
            else:
                verdict = grader.grade(parsed_val, expected, skill["tolerance"])

            attempt_id = self.storage.insert_attempt({
                "session_id": sid,
                "skill_id": skill_id,
                "position_in_session": i,
                "prompt_text": a.get("prompt_text", ""),
                "prompt_audio_ms": a.get("audio_duration_ms"),
                "onset_latency_ms": a.get("onset_latency_ms"),
                "resolution_latency_ms": a.get("resolution_latency_ms"),
                "raw_transcript": a.get("raw_transcript", ""),
                "parsed_answer": parsed_val,
                "answer_mode": a.get("answer_mode") or "typed",
                "expected_answer": expected,
                "correct": verdict["correct"],
                "error_magnitude": verdict["error_magnitude"],
                "skipped": skipped,
                "parameters": a.get("parameters") or {},
                "notes": "offline_sync",
            })
            recorded.append({
                "attempt_id": attempt_id,
                "skill_id": skill_id,
                "client_id": a.get("client_id"),
                "correct": verdict["correct"],
                "rule": verdict["rule"],
            })
            skills_touched[skill_id] = skill["target_latency_ms"]

            # Grade into FSRS at the attempt's own review time (created_at).
            # Skipped attempts must not reach _record_srs. Failures are contained
            # inside _record_srs, so a bad card never breaks the sync.
            if not skipped:
                created_at = _to_float(a.get("created_at"))
                when = (
                    datetime.fromtimestamp(created_at, tz=timezone.utc)
                    if created_at is not None
                    else None
                )
                self._record_srs(
                    user_id=user_id,
                    skill_id=skill_id,
                    parameters=a.get("parameters") or {},
                    correct=verdict["correct"],
                    onset_ms=_to_float(a.get("onset_latency_ms")),
                    resolution_ms=_to_float(a.get("resolution_latency_ms")),
                    target_ms=skill["target_latency_ms"],
                    when=when,
                )

        for skill_id, target_ms in skills_touched.items():
            mastery.update(self.storage, user_id, skill_id, target_ms, self.bus.emit)

        self.storage.end_session(sid)
        synced = sum(1 for r in recorded if r.get("attempt_id") is not None)
        self.bus.emit("attempts.bulk_synced", user_id=user_id, count=synced, session_id=sid)
        return {"session_id": sid, "synced": synced, "attempts": recorded}

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
