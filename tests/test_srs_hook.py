"""Tests for the SRS grade hook (_record_srs) wired into per-attempt sites.

Pattern mirrors test_orchestrator_skip.py: build an Orchestrator with a
FakeStorage that also supports item_state, set up _active directly, drive
through submit_answer or record_manual_skip, then inspect item_state rows.
"""
import unittest
from unittest.mock import patch

from server.orchestrator import Orchestrator
from test_orchestrator_session_plan import FakeBus, FakeStorage


def _active_question():
    """18 - 4 = 14: a<=20 primitive, key=sub:18-4, family=sub.within20."""
    return {
        "qid": "q1",
        "skill_id": "subtraction",
        "mode": "drill",
        "position": 1,
        "expected": 14.0,
        "prompt": "What is 18 minus 4?",
        "audio_duration_ms": 1200,
        "parameters": {"a": 18, "b": 4},
        "user_id": "user-1",
    }


class SrsStorage(FakeStorage):
    """Extends FakeStorage with in-memory item_state support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._item_states: dict[tuple, dict] = {}

    def get_item_state(self, user_id: str, item_key: str) -> dict | None:
        return self._item_states.get((user_id, item_key))

    def upsert_item_state(
        self,
        user_id: str,
        item_key: str,
        tier: str,
        family: str,
        card_json: str,
        due_at: float,
        rating: int,
    ) -> None:
        existing = self._item_states.get((user_id, item_key))
        if existing:
            reps = existing["reps"] + 1
            lapses = existing["lapses"] + (1 if rating == 1 else 0)
        else:
            reps = 1
            lapses = 1 if rating == 1 else 0
        self._item_states[(user_id, item_key)] = {
            "user_id": user_id,
            "item_key": item_key,
            "tier": tier,
            "family": family,
            "card_json": card_json,
            "due_at": due_at,
            "reps": reps,
            "lapses": lapses,
            "last_rating": rating,
        }


class SrsHookTests(unittest.TestCase):
    def _make_orch(self):
        storage = SrsStorage(skill_ids=["subtraction"])
        orch = Orchestrator(storage, FakeBus())
        orch._active["session-1"] = _active_question()
        orch._sessions["session-1"] = {"target": 5}
        return orch, storage

    @patch("server.stt.transcribe", return_value={"text": "fourteen"})
    @patch("server.parser.parse", return_value={"value": 14.0, "skipped": False})
    @patch("server.grader.grade", return_value={"correct": True, "error_magnitude": 0.0, "rule": "exact"})
    @patch("server.mastery.update")
    def test_correct_answer_creates_item_state_with_reps_1(self, _mu, _gr, _pa, _stt):
        """A correct graded answer produces an item_state row with reps=1."""
        orch, storage = self._make_orch()
        orch.submit_answer(
            "session-1", "q1", "/tmp/a.webm",
            prompt_end_ts=100.0, onset_ts=100.5, resolution_ts=101.2,
        )
        row = storage.get_item_state("user-1", "sub:18-4")
        self.assertIsNotNone(row, "item_state row should exist after a graded attempt")
        self.assertEqual(row["reps"], 1)

    @patch("server.stt.transcribe", return_value={"text": "ten"})
    @patch("server.parser.parse", return_value={"value": 10.0, "skipped": False})
    @patch("server.grader.grade", return_value={"correct": False, "error_magnitude": 4.0, "rule": "exact"})
    @patch("server.mastery.update")
    def test_wrong_answer_yields_rating_again(self, _mu, _gr, _pa, _stt):
        """A wrong answer stores last_rating==1 (FSRS Again)."""
        orch, storage = self._make_orch()
        orch.submit_answer(
            "session-1", "q1", "/tmp/a.webm",
            prompt_end_ts=100.0, onset_ts=100.5, resolution_ts=101.2,
        )
        row = storage.get_item_state("user-1", "sub:18-4")
        self.assertIsNotNone(row)
        self.assertEqual(row["last_rating"], 1, "wrong answer should store Rating.Again (1)")

    @patch("server.mastery.update")
    def test_manual_skip_creates_no_item_state(self, _mu):
        """A manual skip must NOT produce an item_state row."""
        orch, storage = self._make_orch()
        orch.record_manual_skip(
            "session-1", "q1",
            prompt_end_ts=100.0, onset_ts=None, resolution_ts=101.0,
        )
        row = storage.get_item_state("user-1", "sub:18-4")
        self.assertIsNone(row, "skipped attempt must not produce an SRS review")


if __name__ == "__main__":
    unittest.main()
