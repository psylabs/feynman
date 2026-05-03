import unittest
from unittest.mock import patch

from server import scheduler


class FakeStorage:
    def __init__(self, skill_ids=None, counts=None):
        self.skill_ids = skill_ids or ["multiplication"]
        self.counts = counts or {}

    def all_attempts_for_user(self, user_id, limit=300):
        return []

    def all_skill_ids(self):
        return self.skill_ids

    def get_skill(self, skill_id):
        return {"id": skill_id, "target_latency_ms": 1800}

    def skill_attempt_count(self, user_id, skill_id):
        return self.counts.get(skill_id, 0)


class SchedulerSessionPlanTests(unittest.TestCase):
    def test_theme_slots_carry_weakness_record_fields(self):
        priority = {
            "fact_key": "mul:7x8",
            "skill_id": "multiplication",
            "display": "7 x 8",
            "median_latency_ms": 6200,
            "target_ms": 1800,
            "accuracy": 1.0,
            "n": 8,
            "gap_ratio": 2.444,
            "priority": 2.444,
        }

        with (
            patch("server.diagnosis.compute_fact_stats", return_value={}),
            patch("server.diagnosis.recent_regressions", return_value=[]),
            patch("server.diagnosis.drill_priorities", return_value=[priority]),
            patch("server.diagnosis.mastered_for_retention", return_value=[]),
        ):
            plan = scheduler.build_session_plan(FakeStorage(), "user-1", 3, lambda *a, **k: None)

        self.assertEqual(plan[0]["fact_key"], "mul:7x8")
        self.assertEqual(plan[0]["target_ms"], 1800)
        self.assertEqual(plan[0]["diagnosis_median_latency_ms"], 6200)
        self.assertEqual(plan[0]["diagnosis_accuracy"], 1.0)
        self.assertEqual(plan[0]["diagnosis_n"], 8)
        self.assertEqual(plan[0]["diagnosis_gap_ratio"], 2.444)

    def test_short_plan_reserves_slot_for_new_skill_exploration(self):
        priority = {
            "fact_key": "sub:3d-2d:n",
            "skill_id": "subtraction",
            "display": "Three-digit - two-digit",
            "median_latency_ms": 9000,
            "target_ms": 5000,
            "accuracy": 0.8,
            "n": 10,
            "gap_ratio": 0.8,
            "priority": 0.8,
        }
        storage = FakeStorage(
            skill_ids=["subtraction", "money_arithmetic"],
            counts={"subtraction": 23, "money_arithmetic": 0},
        )

        with (
            patch("server.diagnosis.compute_fact_stats", return_value={}),
            patch("server.diagnosis.recent_regressions", return_value=[]),
            patch("server.diagnosis.drill_priorities", return_value=[priority]),
            patch("server.diagnosis.mastered_for_retention", return_value=[]),
        ):
            plan = scheduler.build_session_plan(storage, "user-1", 5, lambda *a, **k: None)

        self.assertEqual(len(plan), 5)
        self.assertIn("money_arithmetic", [slot["skill_id"] for slot in plan])
        self.assertEqual(
            [slot["skill_id"] for slot in plan].count("subtraction"),
            4,
        )


if __name__ == "__main__":
    unittest.main()
