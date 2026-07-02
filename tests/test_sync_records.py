import unittest
from unittest.mock import patch

from server.orchestrator import Orchestrator
from server.sync import sync_records


class FakeBus:
    def __init__(self):
        self.events = []

    def emit(self, event, **payload):
        self.events.append((event, payload))


class FakeStorage:
    def __init__(self):
        self.attempts = []
        self.feedback = []
        self.sessions_ended = []

    def create_session(self, user_id, mode="drill"):
        return "sess-1"

    def get_session(self, session_id):
        if session_id == "sess-1":
            return {"id": "sess-1", "user_id": "user-1"}
        return None

    def get_skill(self, skill_id):
        if skill_id != "add":
            return None
        return {"id": "add", "tolerance": {"type": "exact"}, "target_latency_ms": 4000}

    def insert_attempt(self, attempt):
        self.attempts.append(attempt)
        return len(self.attempts)

    def end_session(self, sid):
        self.sessions_ended.append(sid)

    def insert_user_feedback(self, user_id, session_id, attempt_id, thumb, reason, reason_code=None):
        self.feedback.append({
            "user_id": user_id,
            "session_id": session_id,
            "attempt_id": attempt_id,
            "thumb": thumb,
            "reason": reason,
            "reason_code": reason_code,
        })
        return len(self.feedback)


class SyncRecordsTest(unittest.TestCase):
    @patch("server.mastery.update")
    def test_sync_records_maps_offline_feedback_to_synced_attempt(self, _mastery):
        storage = FakeStorage()
        bus = FakeBus()
        orch = Orchestrator(storage, bus)

        result = sync_records(orch, storage, bus, "user-1", [
            {
                "local_id": "outbox-1",
                "kind": "attempt",
                "payload": {
                    "client_id": "attempt-local-1",
                    "skill_id": "add",
                    "prompt_text": "What is 2 plus 2?",
                    "expected_answer": 4,
                    "parsed_answer": 4,
                },
            },
            {
                "local_id": "outbox-2",
                "kind": "review_feedback",
                "payload": {
                    "attempt_client_id": "attempt-local-1",
                    "thumb": 1,
                    "reason": "good card",
                },
            },
        ])

        self.assertEqual(result["synced"], 2)
        self.assertEqual(storage.feedback, [{
            "user_id": "user-1",
            "session_id": "sess-1",
            "attempt_id": 1,
            "thumb": 1,
            "reason": "good card",
            "reason_code": None,
        }])
        self.assertEqual(result["records"][0]["client_id"], "attempt-local-1")
        self.assertEqual(result["records"][0]["server_session_id"], "sess-1")
        self.assertEqual(result["records"][0]["server_attempt_id"], 1)
        self.assertEqual(result["records"][1]["ok"], True)

    @patch("server.mastery.update")
    def test_sync_records_reason_code_round_trip(self, _mastery):
        """reason_code from chip selection is persisted through offline sync."""
        storage = FakeStorage()
        bus = FakeBus()
        orch = Orchestrator(storage, bus)

        result = sync_records(orch, storage, bus, "user-1", [
            {
                "local_id": "outbox-3",
                "kind": "attempt",
                "payload": {
                    "client_id": "attempt-local-3",
                    "skill_id": "add",
                    "prompt_text": "What is 3 plus 3?",
                    "expected_answer": 6,
                    "parsed_answer": 6,
                },
            },
            {
                "local_id": "outbox-4",
                "kind": "review_feedback",
                "payload": {
                    "attempt_client_id": "attempt-local-3",
                    "reason_code": "too_easy",
                },
            },
        ])

        self.assertEqual(result["synced"], 2)
        self.assertEqual(storage.feedback[0]["reason_code"], "too_easy")
        self.assertIsNone(storage.feedback[0]["thumb"])
        self.assertIsNone(storage.feedback[0]["reason"])
        self.assertEqual(result["records"][1]["ok"], True)

    @patch("server.mastery.update")
    def test_sync_records_invalid_reason_code_rejected(self, _mastery):
        """An unknown reason_code in offline sync returns ok=False."""
        storage = FakeStorage()
        bus = FakeBus()
        orch = Orchestrator(storage, bus)

        result = sync_records(orch, storage, bus, "user-1", [
            {
                "local_id": "outbox-5",
                "kind": "review_feedback",
                "payload": {
                    "session_id": "sess-1",
                    "reason_code": "not_a_valid_code",
                },
            },
        ])

        self.assertEqual(result["synced"], 0)
        self.assertEqual(result["records"][0]["ok"], False)
        self.assertEqual(result["records"][0]["reason"], "invalid_reason_code")


if __name__ == "__main__":
    unittest.main()
