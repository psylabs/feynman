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

    def insert_user_feedback(self, user_id, session_id, attempt_id, thumb, reason):
        self.feedback.append({
            "user_id": user_id,
            "session_id": session_id,
            "attempt_id": attempt_id,
            "thumb": thumb,
            "reason": reason,
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
        }])
        self.assertEqual(result["records"][0]["client_id"], "attempt-local-1")
        self.assertEqual(result["records"][0]["server_session_id"], "sess-1")
        self.assertEqual(result["records"][0]["server_attempt_id"], 1)
        self.assertEqual(result["records"][1]["ok"], True)


if __name__ == "__main__":
    unittest.main()
