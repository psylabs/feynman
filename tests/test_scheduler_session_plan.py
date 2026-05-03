import unittest
from unittest.mock import patch

from server import scheduler


class FakeStorage:
    def all_attempts_for_user(self, user_id, limit=300):
        return []

    def all_skill_ids(self):
        return ["multiplication"]

    def get_skill(self, skill_id):
        return {"id": skill_id, "target_latency_ms": 1800}


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


if __name__ == "__main__":
    unittest.main()
