import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from server import srs
from server.orchestrator import Orchestrator


class FakeBus:
    def emit(self, *a, **k):
        pass


class FakeStorage:
    def __init__(self):
        self.inserted = []
        self.sessions_ended = []
        self._item_states = {}

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

    # ---- item_state (FSRS) -------------------------------------------------
    def get_item_state(self, user_id, item_key):
        return self._item_states.get((user_id, item_key))

    def upsert_item_state(self, user_id, item_key, tier, family, card_json, due_at, rating):
        existing = self._item_states.get((user_id, item_key))
        reps = (existing["reps"] + 1) if existing else 1
        self._item_states[(user_id, item_key)] = {
            "user_id": user_id,
            "item_key": item_key,
            "tier": tier,
            "family": family,
            "card_json": card_json,
            "due_at": due_at,
            "reps": reps,
            "last_rating": rating,
        }


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


class BulkFsrsGradingTest(unittest.TestCase):
    """Fix 2: offline bulk-synced attempts must grade into FSRS using each
    attempt's own created_at, not 'now'."""

    def setUp(self):
        self.storage = FakeStorage()
        self.orch = Orchestrator(self.storage, FakeBus())

    @patch("server.mastery.update")
    def test_correct_synced_attempt_creates_item_state_reps_1(self, _m):
        # mul:7x8 primitive; correct, fast onset -> Rating.Good.
        past = time.time() - 30 * 86400  # 30 days ago
        self.orch.record_bulk_attempts("user-1", [{
            "skill_id": "multiplication",
            "expected_answer": 56,
            "parsed_answer": 56,
            "parameters": {"a": 7, "b": 8},
            "onset_latency_ms": 800,
            "resolution_latency_ms": 900,
            "created_at": past,
        }])
        row = self.storage.get_item_state("user-1", "mul:7x8")
        self.assertIsNotNone(row, "correct synced attempt should create an item_state")
        self.assertEqual(row["reps"], 1)

    @patch("server.mastery.update")
    def test_due_at_derived_from_historical_created_at(self, _m):
        # A review graded at a past created_at is due earlier than the same
        # review graded 'now' (same interval, earlier anchor).
        past = time.time() - 30 * 86400
        self.orch.record_bulk_attempts("user-1", [{
            "skill_id": "multiplication",
            "expected_answer": 56,
            "parsed_answer": 56,
            "parameters": {"a": 7, "b": 8},
            "onset_latency_ms": 800,
            "resolution_latency_ms": 900,
            "created_at": past,
        }])
        row = self.storage.get_item_state("user-1", "mul:7x8")

        # Equivalent review graded now: onset 800 <= 1200 target -> Good.
        _, now_due = srs.review(None, srs.rate(True, 800, 1200), datetime.now(timezone.utc))
        self.assertLess(
            row["due_at"], now_due,
            "historical created_at should yield an earlier due_at than a now-grade",
        )

    @patch("server.mastery.update")
    def test_skipped_synced_attempt_creates_no_item_state(self, _m):
        self.orch.record_bulk_attempts("user-1", [{
            "skill_id": "multiplication",
            "expected_answer": 56,
            "parameters": {"a": 7, "b": 8},
            "skipped": True,
            "created_at": time.time() - 86400,
        }])
        self.assertIsNone(self.storage.get_item_state("user-1", "mul:7x8"))


if __name__ == "__main__":
    unittest.main()
