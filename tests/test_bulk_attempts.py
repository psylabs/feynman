import unittest
from unittest.mock import patch

from server.orchestrator import Orchestrator


class FakeBus:
    def emit(self, *a, **k):
        pass


class FakeStorage:
    def __init__(self):
        self.inserted = []
        self.sessions_ended = []

    def create_session(self, user_id, mode="drill"):
        return "sess-1"

    def get_skill(self, skill_id):
        if skill_id == "unknown":
            return None
        return {"id": skill_id, "tolerance": {"type": "exact"}, "target_latency_ms": 4000}

    def insert_attempt(self, a):
        self.inserted.append(a)
        return len(self.inserted)

    def end_session(self, sid):
        self.sessions_ended.append(sid)


class BulkAttemptsTest(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorage()
        self.orch = Orchestrator(self.storage, FakeBus())

    @patch("server.mastery.update")
    def test_regrades_and_records(self, mock_mastery):
        attempts = [
            {"skill_id": "add", "expected_answer": 4, "parsed_answer": 4},      # correct
            {"skill_id": "add", "expected_answer": 4, "parsed_answer": 5},      # wrong
            {"skill_id": "sub", "expected_answer": 7, "skipped": True},          # skipped
            {"skill_id": "unknown", "expected_answer": 1, "parsed_answer": 1},   # dropped
            {"skill_id": "add", "parsed_answer": 4},                             # no expected -> dropped
        ]
        res = self.orch.record_bulk_attempts("user-1", attempts)

        self.assertEqual(res["synced"], 3)
        self.assertEqual(len(self.storage.inserted), 3)
        self.assertEqual(self.storage.inserted[0]["correct"], True)
        self.assertEqual(self.storage.inserted[1]["correct"], False)
        self.assertEqual(self.storage.inserted[2]["skipped"], True)
        self.assertEqual(self.storage.inserted[2]["correct"], False)
        # mastery recomputed once per touched skill (add, sub) — not for dropped
        self.assertEqual(mock_mastery.call_count, 2)
        self.assertEqual(self.storage.sessions_ended, ["sess-1"])

    @patch("server.mastery.update")
    def test_coerces_string_numbers(self, _m):
        res = self.orch.record_bulk_attempts(
            "user-1", [{"skill_id": "add", "expected_answer": "4", "parsed_answer": "4"}]
        )
        self.assertEqual(res["synced"], 1)
        self.assertEqual(self.storage.inserted[0]["correct"], True)

    @patch("server.mastery.update")
    def test_records_offline_attempts_as_typed(self, _m):
        self.orch.record_bulk_attempts(
            "user-1", [{"skill_id": "add", "expected_answer": 4, "parsed_answer": 4}]
        )

        self.assertEqual(self.storage.inserted[0]["answer_mode"], "typed")

    @patch("server.stt.transcribe", return_value={"text": "4"})
    @patch("server.mastery.update")
    def test_records_online_audio_attempts_as_voice(self, _mastery, _stt):
        sid = "sess-1"
        self.orch._sessions[sid] = {"target": 5}
        self.orch._active[sid] = {
            "qid": "q1",
            "user_id": "user-1",
            "skill_id": "add",
            "position": 1,
            "prompt": "What is 2 plus 2?",
            "audio_duration_ms": 900,
            "expected": 4,
            "parameters": {"a": 2, "b": 2},
            "mode": "drill",
        }

        self.orch.submit_answer(sid, "q1", "/tmp/answer.wav", 10.0, 10.2, 11.5)

        self.assertEqual(self.storage.inserted[0]["answer_mode"], "voice")


if __name__ == "__main__":
    unittest.main()
