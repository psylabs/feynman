import unittest
from unittest.mock import patch

from server.orchestrator import Orchestrator


class FakeBus:
    def emit(self, *args, **kwargs):
        pass


class FakeStorage:
    def __init__(self, attempts=None, skill_ids=None):
        self.sid = "session-1"
        self.attempts = attempts or [
            {"position_in_session": 1, "correct": 1, "resolution_latency_ms": 4200},
            {"position_in_session": 2, "correct": 0, "resolution_latency_ms": 7100},
        ]
        self.skill_ids = skill_ids or []

    def get_user(self, user_id):
        return {"id": user_id, "name": "Mo"} if user_id == "user-1" else None

    def ensure_skill_states_for_user(self, user_id):
        pass

    def create_session(self, user_id, mode):
        return self.sid

    def get_session(self, sid):
        return {"id": sid, "user_id": "user-1", "mode": "drill"}

    def end_session(self, sid):
        pass

    def session_attempts(self, sid):
        return self.attempts

    def all_attempts_for_user(self, user_id, limit=300):
        return self.attempts

    def all_skill_ids(self):
        return self.skill_ids

    def get_skill(self, skill_id):
        return {"id": skill_id, "target_latency_ms": 5000, "tolerance": {"type": "exact"}}

    def insert_attempt(self, attempt):
        self.last_attempt = attempt
        return 123

    def skill_attempt_count(self, user_id, skill_id):
        return 1

    def update_skill_state(self, user_id, skill_id, state):
        pass


class OrchestratorSessionPlanTests(unittest.TestCase):
    def test_start_and_end_include_plan_context(self):
        plan = [
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8"},
        ]
        orch = Orchestrator(FakeStorage(), FakeBus())

        with patch("server.scheduler.build_session_plan", return_value=plan):
            started = orch.start_session("user-1", "drill", target_questions=2)
            ended = orch.end_session("session-1")

        self.assertEqual(started["session_plan"]["focus"], ["7 x 8"])
        self.assertEqual(started["session_plan"]["counts"]["theme"], 2)
        self.assertEqual(ended["session_analysis"]["plan"]["focus"], ["7 x 8"])
        self.assertEqual(ended["session_analysis"]["focus_stats"][0]["correct"], 1)
        self.assertEqual(ended["session_analysis"]["focus_stats"][0]["total"], 2)

    def test_start_includes_exploratory_plan_when_no_priorities_exist(self):
        orch = Orchestrator(FakeStorage(), FakeBus())

        with patch("server.scheduler.build_session_plan", return_value=[]):
            started = orch.start_session("user-1", "drill", target_questions=2)

        self.assertEqual(started["session_plan"]["focus"], [])
        self.assertIn("exploratory", started["session_plan"]["intent"])

    def test_end_includes_latency_analysis_for_exploratory_session(self):
        attempts = [
            {
                "position_in_session": 1,
                "skill_id": "multiplication",
                "parameters": {"a": 12, "b": 9},
                "correct": 1,
                "onset_latency_ms": 1200,
                "resolution_latency_ms": 6500,
            },
            {
                "position_in_session": 2,
                "skill_id": "multiplication",
                "parameters": {"a": 7, "b": 12},
                "correct": 1,
                "onset_latency_ms": 800,
                "resolution_latency_ms": 3500,
            },
        ]
        orch = Orchestrator(FakeStorage(attempts=attempts, skill_ids=["multiplication"]), FakeBus())

        with patch("server.scheduler.build_session_plan", return_value=[]):
            orch.start_session("user-1", "drill", target_questions=2)
            ended = orch.end_session("session-1")

        self.assertIsNotNone(ended["session_analysis"])
        self.assertIn("exploratory", ended["session_analysis"]["plan"]["intent"])
        self.assertEqual(ended["session_analysis"]["fluency_gaps"][0]["fact_key"], "mul:9x12")
        self.assertEqual(ended["session_analysis"]["fluency_gaps"][0]["target_ms"], 5000)

    def test_skinned_slot_covers_reaches_record_srs(self):
        """A plan slot carrying `covers` must flow into `_record_srs` as
        `item_key_override` so the skeleton item is credited too."""
        orch = Orchestrator(FakeStorage(), FakeBus())
        orch._sessions["session-1"] = {"target": 2}
        orch._active["session-1"] = {
            "qid": "q-1",
            "user_id": "user-1",
            "mode": "drill",
            "skill_id": "money_arithmetic",
            "prompt": "How much is the tip?",
            "expected": 4.0,
            "parameters": {"operation": "restaurant_tip_15"},
            "audio_duration_ms": 1000,
            "position": 1,
            "covers": "pct:15",
        }
        captured = {}

        def _fake_record_srs(*args, **kwargs):
            captured["override"] = kwargs.get("item_key_override")

        with (
            patch("server.stt.transcribe", return_value={"text": "four"}),
            patch("server.parser.parse", return_value={"value": 4.0, "skipped": False}),
            patch("server.grader.grade", return_value={"correct": True, "error_magnitude": 0.0, "rule": "exact"}),
            patch("server.mastery.update"),
            patch.object(orch, "_record_srs", side_effect=_fake_record_srs),
        ):
            orch.submit_answer("session-1", "q-1", "answer.webm", 1.0, 2.0, 3.0)

        self.assertEqual(captured["override"], "pct:15")

    def test_submit_does_not_request_mid_drill_feedback(self):
        orch = Orchestrator(FakeStorage(), FakeBus())
        orch._sessions["session-1"] = {"target": 2}
        orch._active["session-1"] = {
            "qid": "q-1",
            "user_id": "user-1",
            "mode": "drill",
            "skill_id": "addition",
            "prompt": "What is 2 plus 2?",
            "expected": 4.0,
            "parameters": {"a": 2, "b": 2},
            "audio_duration_ms": 1000,
            "position": 1,
        }

        with (
            patch("server.stt.transcribe", return_value={"text": "five"}),
            patch("server.parser.parse", return_value={"value": 5.0, "skipped": False}),
            patch("server.grader.grade", return_value={"correct": False, "error_magnitude": 1.0, "rule": "exact"}),
            patch("server.mastery.update"),
        ):
            result = orch.submit_answer("session-1", "q-1", "answer.webm", 1.0, 2.0, 3.0)

        self.assertFalse(result["feedback_pending"])


if __name__ == "__main__":
    unittest.main()
