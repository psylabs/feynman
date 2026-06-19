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


if __name__ == "__main__":
    unittest.main()
