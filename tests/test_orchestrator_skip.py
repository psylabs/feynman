import unittest
from unittest.mock import patch

from server.orchestrator import Orchestrator
from test_orchestrator_session_plan import FakeBus, FakeStorage


def _active_question():
    return {
        "qid": "q1",
        "skill_id": "subtraction",
        "mode": "drill",
        "position": 1,
        "expected": 8.0,
        "prompt": "What is 17 minus 9?",
        "audio_duration_ms": 1200,
        "parameters": {"a": 17, "b": 9},
        "user_id": "user-1",
    }


class OrchestratorSkipTests(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorage(skill_ids=["subtraction"])
        self.orch = Orchestrator(self.storage, FakeBus())
        self.orch._active["session-1"] = _active_question()
        self.orch._sessions["session-1"] = {"target": 5}

    @patch("server.mastery.update")
    def test_manual_skip_records_tagged_skipped_attempt(self, _mu):
        out = self.orch.record_manual_skip("session-1", "q1", 100.0, None, 101.0, {})
        self.assertTrue(out["skipped"])
        self.assertTrue(out["manual_skip"])
        self.assertEqual(out["rule"], "skipped")
        self.assertEqual(self.storage.last_attempt["answer_mode"], "manual_skip")
        self.assertTrue(self.storage.last_attempt["skipped"])
        self.assertIsNone(self.storage.last_attempt["parsed_answer"])
        # The active question is consumed so the next one can load.
        self.assertNotIn("session-1", self.orch._active)

    @patch("server.mastery.update")
    @patch("server.stt.transcribe", return_value={"text": "skip", "elapsed_ms": 10})
    def test_stt_heard_skip_routes_to_retry_not_a_recorded_skip(self, _stt, _mu):
        out = self.orch.submit_answer(
            "session-1", "q1", "/tmp/a.webm", 100.0, 100.5, 101.0, {}
        )
        self.assertTrue(out["audio_failed"])
        self.assertNotIn("attempt_id", out)
        self.assertFalse(hasattr(self.storage, "last_attempt"))
        # Question stays active for the retry.
        self.assertIn("session-1", self.orch._active)


if __name__ == "__main__":
    unittest.main()
