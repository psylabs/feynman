import unittest
from unittest.mock import patch

from server import scheduler


class FakeStorage:
    def __init__(self, skill_ids=None, counts=None, attempts=None):
        self.skill_ids = skill_ids or ["multiplication"]
        self.counts = counts or {}
        self.attempts = attempts or []

    def all_attempts_for_user(self, user_id, limit=300):
        return self.attempts

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

    def test_session_plan_allocates_half_to_grounded(self):
        """A length-12 plan reserves ~50% of slots for grounded skills
        (money + weather). Foundation slots come from drill_priorities
        filtered to FOUNDATION_SKILLS."""
        priority = {
            "fact_key": "sub:2d-1d:b",
            "skill_id": "subtraction",
            "display": "Two-digit − single-digit, with borrow",
            "median_latency_ms": 9000,
            "target_ms": 3500,
            "accuracy": 0.8,
            "n": 10,
            "gap_ratio": 1.57,
            "priority": 1.57,
        }
        storage = FakeStorage(
            skill_ids=["subtraction", "money_arithmetic", "weather_math"],
            counts={"subtraction": 23, "money_arithmetic": 4, "weather_math": 0},
        )

        with (
            patch("server.diagnosis.compute_fact_stats", return_value={}),
            patch("server.diagnosis.recent_regressions", return_value=[]),
            patch("server.diagnosis.drill_priorities", return_value=[priority]),
            patch("server.diagnosis.mastered_for_retention", return_value=[]),
        ):
            plan = scheduler.build_session_plan(storage, "user-1", 12, lambda *a, **k: None)

        self.assertEqual(len(plan), 12)
        roles = [slot["role"] for slot in plan]
        self.assertEqual(roles.count("grounded"), 6)
        # Grounded slots cover both grounded skills, weighted toward weather
        # (the under-sampled one).
        grounded_skills = [s["skill_id"] for s in plan if s["role"] == "grounded"]
        self.assertIn("weather_math", grounded_skills)
        self.assertIn("money_arithmetic", grounded_skills)
        self.assertGreaterEqual(grounded_skills.count("weather_math"),
                                grounded_skills.count("money_arithmetic"))
        # Foundation slots fill the remainder with the priority's skill.
        foundation = [s for s in plan if s["role"] in ("theme", "related")]
        self.assertGreaterEqual(len(foundation), 5)
        self.assertTrue(all(s["skill_id"] == "subtraction" for s in foundation
                            if s["role"] == "theme"))

    def test_session_plan_returns_only_grounded_on_cold_start(self):
        """No prior priorities → return grounded-only seed if any grounded
        skill exists. Live pick_drill handles the rest."""
        storage = FakeStorage(
            skill_ids=["addition", "money_arithmetic"],
            counts={},
        )

        with (
            patch("server.diagnosis.compute_fact_stats", return_value={}),
            patch("server.diagnosis.recent_regressions", return_value=[]),
            patch("server.diagnosis.drill_priorities", return_value=[]),
            patch("server.diagnosis.mastered_for_retention", return_value=[]),
        ):
            plan = scheduler.build_session_plan(storage, "user-1", 8, lambda *a, **k: None)

        self.assertEqual(len(plan), 4)  # length // 2
        self.assertTrue(all(s["role"] == "grounded" for s in plan))
        self.assertTrue(all(s["skill_id"] == "money_arithmetic" for s in plan))


    def _finance_plan(self, attempts):
        storage = FakeStorage(
            skill_ids=["addition", "money_arithmetic"],
            counts={"addition": 20, "money_arithmetic": 4},
            attempts=attempts,
        )
        with (
            patch("server.diagnosis.compute_fact_stats", return_value={}),
            patch("server.diagnosis.recent_regressions", return_value=[]),
            patch("server.diagnosis.drill_priorities", return_value=[]),
            patch("server.diagnosis.mastered_for_retention", return_value=[]),
        ):
            return scheduler.build_session_plan(storage, "u", 8, lambda *a, **k: None)

    def test_enforces_tip_and_split_finance_slots(self):
        plan = self._finance_plan([])
        ops = [s["target_fact"].get("operation") for s in plan
               if s["skill_id"] == "money_arithmetic" and isinstance(s["target_fact"], dict)]
        self.assertIn("restaurant_tip_15", ops)
        self.assertIn("split_bill", ops)

    def test_split_bill_capped_until_unlocked(self):
        plan = self._finance_plan([])
        split = next(s for s in plan if (s["target_fact"] or {}).get("operation") == "split_bill")
        self.assertEqual(split["target_fact"]["max_party"], scheduler.SPLIT_BILL_BASE_MAX)

    def test_split_bill_unlocks_advanced_when_fluent(self):
        attempts = [
            {"skill_id": "money_arithmetic", "correct": 1, "resolution_latency_ms": 1500,
             "parameters": {"operation": "split_bill", "people": 4}}
            for _ in range(8)
        ]
        plan = self._finance_plan(attempts)
        split = next(s for s in plan if (s["target_fact"] or {}).get("operation") == "split_bill")
        self.assertEqual(split["target_fact"]["max_party"], scheduler.SPLIT_BILL_ADVANCED_MAX)

    def test_category_share_not_in_grounded_ops(self):
        """category_share operation should not be schedulable (retired)."""
        self.assertNotIn("category_share", scheduler._GROUNDED_OPS["money_arithmetic"])


if __name__ == "__main__":
    unittest.main()
