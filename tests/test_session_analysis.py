import unittest

from server import session_analysis


class SessionAnalysisTests(unittest.TestCase):
    def test_plan_summary_names_focus_and_mix(self):
        slots = [
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "theme", "fact_key": "add:2d+2d:c", "display": "Two-digit + two-digit, with carry"},
            {"role": "related", "fact_key": "mul:7x7", "display": "7 x 7"},
            {"role": "retention", "fact_key": "mul:5x9", "display": "5 x 9"},
        ]

        summary = session_analysis.plan_summary(slots)

        self.assertEqual(summary["focus"], ["7 x 8", "Two-digit + two-digit, with carry"])
        self.assertEqual(summary["counts"], {"theme": 3, "related": 1, "retention": 1})
        self.assertIn("7 x 8", summary["intent"])
        self.assertIn("3 focused", summary["mix"])

    def test_review_analysis_compares_attempts_to_planned_roles(self):
        slots = [
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "related", "fact_key": "mul:7x7", "display": "7 x 7"},
        ]
        attempts = [
            {"position_in_session": 1, "correct": 1, "resolution_latency_ms": 5200},
            {"position_in_session": 2, "correct": 0, "resolution_latency_ms": 7800},
            {"position_in_session": 3, "correct": 1, "resolution_latency_ms": 3100},
        ]

        analysis = session_analysis.review_analysis(slots, attempts)

        self.assertEqual(analysis["role_stats"]["theme"]["correct"], 1)
        self.assertEqual(analysis["role_stats"]["theme"]["total"], 2)
        self.assertEqual(analysis["role_stats"]["theme"]["median_latency_ms"], 6500)
        self.assertEqual(analysis["focus_stats"][0]["display"], "7 x 8")
        self.assertEqual(analysis["focus_stats"][0]["correct"], 1)
        self.assertEqual(analysis["focus_stats"][0]["total"], 2)
        self.assertIn("7 x 8", analysis["still_weak"][0])

    def test_review_analysis_flags_correct_answers_that_are_still_slow(self):
        slots = [
            {
                "role": "theme",
                "skill_id": "multiplication",
                "fact_key": "mul:7x8",
                "display": "7 x 8",
                "target_ms": 1800,
                "diagnosis_median_latency_ms": 6200,
                "diagnosis_n": 8,
            },
            {
                "role": "theme",
                "skill_id": "multiplication",
                "fact_key": "mul:7x8",
                "display": "7 x 8",
                "target_ms": 1800,
                "diagnosis_median_latency_ms": 6200,
                "diagnosis_n": 8,
            },
        ]
        attempts = [
            {
                "position_in_session": 1,
                "correct": 1,
                "onset_latency_ms": 900,
                "resolution_latency_ms": 5600,
            },
            {
                "position_in_session": 2,
                "correct": 1,
                "onset_latency_ms": 1100,
                "resolution_latency_ms": 5000,
            },
        ]

        analysis = session_analysis.review_analysis(slots, attempts)

        gap = analysis["fluency_gaps"][0]
        self.assertEqual(gap["fact_key"], "mul:7x8")
        self.assertEqual(gap["skill_id"], "multiplication")
        self.assertEqual(gap["target_ms"], 1800)
        self.assertEqual(gap["median_correct_latency_ms"], 5300)
        self.assertEqual(gap["baseline_median_latency_ms"], 6200)
        self.assertEqual(gap["baseline_n"], 8)
        self.assertGreater(gap["gap_ratio"], 1.0)
        self.assertEqual(gap["interpretation"], "calculation effort")
        self.assertIn("correct but still slow", analysis["still_weak"][0])
        self.assertFalse(analysis["moved"])

    def test_review_analysis_surfaces_slowest_correct_attempts(self):
        slots = [
            {"role": "theme", "skill_id": "multiplication", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 1800},
            {"role": "theme", "skill_id": "multiplication", "fact_key": "mul:8x8", "display": "8 x 8", "target_ms": 1800},
            {"role": "related", "skill_id": "multiplication", "fact_key": "mul:7x7", "display": "7 x 7", "target_ms": 1800},
        ]
        attempts = [
            {"position_in_session": 1, "correct": 1, "resolution_latency_ms": 6100},
            {"position_in_session": 2, "correct": 0, "resolution_latency_ms": 9000},
            {"position_in_session": 3, "correct": 1, "resolution_latency_ms": 3200},
        ]

        analysis = session_analysis.review_analysis(slots, attempts)

        self.assertEqual(analysis["slowest_correct"][0]["fact_key"], "mul:7x8")
        self.assertEqual(analysis["slowest_correct"][0]["latency_ms"], 6100)
        self.assertEqual(analysis["slowest_correct"][1]["fact_key"], "mul:7x7")


if __name__ == "__main__":
    unittest.main()
