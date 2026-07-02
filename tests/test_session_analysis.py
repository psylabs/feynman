import unittest

from server import session_analysis


class SessionAnalysisTests(unittest.TestCase):
    def test_plan_summary_names_focus_and_mix(self):
        slots = [
            {"role": "theme", "skill_id": "multiplication", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "theme", "skill_id": "multiplication", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "theme", "skill_id": "addition", "fact_key": "add:2d+2d:c", "display": "Two-digit + two-digit, with carry"},
            {"role": "related", "skill_id": "multiplication", "fact_key": "mul:7x7", "display": "7 x 7"},
            {"role": "retention", "skill_id": "multiplication", "fact_key": "mul:5x9", "display": "5 x 9"},
            {"role": "grounded", "skill_id": "weather_math", "fact_key": "weather:temp_delta", "display": "Weather: temp delta"},
            {"role": "grounded", "skill_id": "money_arithmetic", "fact_key": "money:charge_total", "display": "Money: charge total"},
        ]

        summary = session_analysis.plan_summary(slots)

        self.assertEqual(summary["focus"], ["7 x 8", "Two-digit + two-digit, with carry"])
        self.assertEqual(
            summary["counts"],
            {"theme": 3, "related": 1, "retention": 1, "grounded": 2, "exploration": 0},
        )
        self.assertEqual(
            summary["grounded_skills"],
            ["money_arithmetic", "weather_math"],
        )
        self.assertIn("7 x 8", summary["intent"])
        self.assertIn("real-life drills", summary["intent"])
        self.assertIn("3 focused", summary["mix"])
        self.assertIn("2 real-life", summary["mix"])

    def test_review_analysis_compares_attempts_to_planned_roles(self):
        slots = [
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "theme", "fact_key": "mul:7x8", "display": "7 x 8", "target_ms": 5000},
            {"role": "related", "fact_key": "mul:7x7", "display": "7 x 7"},
        ]
        attempts = [
            {"position_in_session": 1, "correct": 1, "resolution_latency_ms": 5200},
            {"position_in_session": 2, "correct": 0, "resolution_latency_ms": 7800},
            {"position_in_session": 3, "correct": 0, "resolution_latency_ms": 7800},
            {"position_in_session": 4, "correct": 0, "resolution_latency_ms": 7800},
            {"position_in_session": 5, "correct": 0, "resolution_latency_ms": 7800},
            {"position_in_session": 6, "correct": 1, "resolution_latency_ms": 3100},
        ]

        analysis = session_analysis.review_analysis(slots, attempts)

        self.assertEqual(analysis["role_stats"]["theme"]["correct"], 1)
        self.assertEqual(analysis["role_stats"]["theme"]["total"], 5)
        self.assertEqual(analysis["focus_stats"][0]["display"], "7 x 8")
        self.assertEqual(analysis["focus_stats"][0]["correct"], 1)
        self.assertEqual(analysis["focus_stats"][0]["total"], 5)
        self.assertIn("7 x 8", analysis["still_weak"][0])

    def test_review_analysis_flags_correct_answers_that_are_still_slow(self):
        _slot = {
            "role": "theme",
            "skill_id": "multiplication",
            "fact_key": "mul:7x8",
            "display": "7 x 8",
            "target_ms": 1800,
            "diagnosis_median_latency_ms": 6200,
            "diagnosis_n": 8,
        }
        slots = [_slot] * 5
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
            {
                "position_in_session": 3,
                "correct": 1,
                "onset_latency_ms": 1000,
                "resolution_latency_ms": 5300,
            },
            {
                "position_in_session": 4,
                "correct": 1,
                "onset_latency_ms": 950,
                "resolution_latency_ms": 5100,
            },
            {
                "position_in_session": 5,
                "correct": 1,
                "onset_latency_ms": 1050,
                "resolution_latency_ms": 5400,
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


class RewrittenSchedulerRoleTests(unittest.TestCase):
    """The rewritten scheduler (server.scheduler.build_session_plan) emits
    roles 'due'/'bootstrap'/'weak' (optionally '+skin'), not the pre-rewrite
    'theme'/'related'/'grounded'/'retention'/'exploration'. plan_summary must
    describe these sessions accurately instead of falling back to the
    "exploratory" copy."""

    def _slots(self):
        return [
            {"role": "due", "skill_id": "multiplication", "fact_key": "mul:12x3",
             "display": "12 x 3", "family": "mul.x12"},
            {"role": "due", "skill_id": "multiplication", "fact_key": "mul:12x7",
             "display": "12 x 7", "family": "mul.x12"},
            {"role": "due", "skill_id": "subtraction", "fact_key": "sub:15-7",
             "display": "15 - 7", "family": "sub.within20"},
            {"role": "due", "skill_id": "subtraction", "fact_key": "sub:13-6",
             "display": "13 - 6", "family": "sub.within20"},
            {"role": "bootstrap", "skill_id": "division", "fact_key": "div:16x8",
             "display": "16 / 8", "family": "div.x8"},
            {"role": "weak+skin", "skill_id": "money_arithmetic", "fact_key": "money:charge_total",
             "display": "Money: charge total", "family": "add.2d+2d.n"},
        ]

    def test_counts_understand_due_bootstrap_weak_and_skins(self):
        summary = session_analysis.plan_summary(self._slots())

        self.assertEqual(summary["counts"]["due"], 4)
        self.assertEqual(summary["counts"]["bootstrap"], 1)
        self.assertEqual(summary["counts"]["weak"], 1)
        # A '+skin' slot renders as a money/weather problem: it counts once
        # for its base role (weak) and once toward the grounded display bucket.
        self.assertEqual(summary["counts"]["grounded"], 1)

    def test_due_majority_session_reads_as_clearing_reviews_not_exploratory(self):
        summary = session_analysis.plan_summary(self._slots())

        self.assertIn("overdue reviews", summary["intent"])
        self.assertIn("×12 table", summary["intent"])
        self.assertIn("first look", summary["intent"])
        self.assertNotIn("exploratory", summary["intent"])

    def test_mix_sentence_lists_new_role_labels(self):
        summary = session_analysis.plan_summary(self._slots())

        # 4 due + 1 bootstrap + 1 weak (from weak+skin) + 1 grounded (from weak+skin also counting toward grounded)
        self.assertEqual(summary["mix"], "Mix: 4 reviews due, 1 first look, 1 weak-spot drill, 1 real-life.")

    def test_mix_sentence_singular_forms(self):
        """Test singular forms for new roles when count is 1."""
        slots = [
            {"role": "due", "skill_id": "multiplication", "fact_key": "mul:12x3",
             "display": "12 x 3", "family": "mul.x12"},
            {"role": "weak", "skill_id": "subtraction", "fact_key": "sub:15-7",
             "display": "15 - 7", "family": "sub.within20"},
        ]
        summary = session_analysis.plan_summary(slots)

        self.assertEqual(summary["mix"], "Mix: 1 review due, 1 weak-spot drill.")

    def test_weak_majority_session_reads_as_drilling_weak_spots(self):
        slots = [
            {"role": "weak", "skill_id": "subtraction", "fact_key": "sub:15-7",
             "display": "15 - 7", "family": "sub.within20"},
            {"role": "weak", "skill_id": "subtraction", "fact_key": "sub:13-6",
             "display": "13 - 6", "family": "sub.within20"},
            {"role": "due", "skill_id": "multiplication", "fact_key": "mul:12x3",
             "display": "12 x 3", "family": "mul.x12"},
        ]
        summary = session_analysis.plan_summary(slots)
        self.assertIn("drilling weak spots", summary["intent"])

    def test_only_falls_back_to_exploratory_copy_with_no_recognized_slots(self):
        summary = session_analysis.plan_summary([])
        self.assertIn("exploratory", summary["intent"])
        self.assertEqual(summary["mix"], "Mix: exploratory.")

    def test_legacy_theme_plan_copy_is_unchanged(self):
        slots = [
            {"role": "theme", "skill_id": "multiplication", "fact_key": "mul:7x8", "display": "7 x 8"},
            {"role": "related", "skill_id": "multiplication", "fact_key": "mul:7x7", "display": "7 x 7"},
        ]
        summary = session_analysis.plan_summary(slots)
        self.assertEqual(summary["focus"], ["7 x 8"])
        self.assertIn("mainly drilling 7 x 8", summary["intent"])
        self.assertNotIn("overdue reviews", summary["intent"])


class PatternAnalysisTests(unittest.TestCase):
    def _attempt(self, skill_id, latency_ms, correct=1, **feature_extra):
        f = {"abs_diff": 5, "min_operand": 3, "max_operand": 8}
        f.update(feature_extra)
        return {
            "skill_id": skill_id,
            "resolution_latency_ms": latency_ms,
            "correct": correct,
            "parameters": {"features": f},
        }

    def test_detects_slow_borrow_pattern(self):
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 4000, has_borrow=True) for _ in range(8)]
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertTrue(len(results) > 0)
        top = results[0]
        self.assertEqual(top["feature_key"], "has_borrow")
        self.assertEqual(top["feature_value"], True)
        self.assertGreater(top["ratio"], 1.4)
        self.assertEqual(top["n"], 8)

    def test_ignores_groups_below_min_n(self):
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 5000, has_borrow=True) for _ in range(4)]
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertEqual(results, [])

    def test_ignores_low_ratio(self):
        attempts = (
            [self._attempt("subtraction", 1000, has_borrow=False) for _ in range(10)] +
            [self._attempt("subtraction", 1300, has_borrow=True) for _ in range(6)]
        )
        results = session_analysis.pattern_analysis(attempts)
        self.assertEqual(results, [])

    def test_returns_at_most_3(self):
        attempts = []
        for i in range(4):
            attempts += [self._attempt("subtraction", 1000) for _ in range(10)]
            attempts += [self._attempt(f"skill_{i}", 5000, **{f"feat_{i}": True}) for _ in range(6)]
        results = session_analysis.pattern_analysis(attempts)
        self.assertLessEqual(len(results), 3)


class StratificationTests(unittest.TestCase):
    def _sub(self, borrow, latency=1000):
        return {
            "skill_id": "subtraction", "correct": 1,
            "resolution_latency_ms": latency,
            "parameters": {"borrow": borrow},
        }

    def test_splits_subtraction_by_borrow(self):
        attempts = [self._sub(False)] * 3 + [self._sub(True)] * 3
        strat = session_analysis._session_stratification(attempts)
        self.assertEqual(len(strat), 1)
        self.assertEqual(strat[0]["skill_id"], "subtraction")
        labels = {r["label"] for r in strat[0]["rows"]}
        self.assertEqual(labels, {"with borrow", "no borrow"})

    def test_omits_skill_when_one_bucket_too_small(self):
        attempts = [self._sub(False)] * 3 + [self._sub(True)] * 1
        strat = session_analysis._session_stratification(attempts)
        self.assertEqual(strat, [])


class WeakLinesThresholdTests(unittest.TestCase):
    def _focus_stat(self, correct, total):
        return {
            "display": "test", "total": total, "correct": correct,
            "accuracy": correct / total if total else None,
            "fluency_status": "slow" if correct == total else "unknown",
            "median_correct_latency_ms": 3000, "target_ms": 1000,
        }

    def test_weak_lines_requires_min_5_attempts(self):
        stat = self._focus_stat(2, 3)
        lines = session_analysis._weak_lines([stat])
        self.assertEqual(lines, [])

    def test_weak_lines_fires_at_min_5(self):
        stat = self._focus_stat(3, 5)
        lines = session_analysis._weak_lines([stat])
        self.assertEqual(len(lines), 1)


class TipAttachmentTests(unittest.TestCase):
    """Verify strategy tips are attached to wrong/slow attempts by review_analysis."""

    # A simple subtraction that triggers strat_count_up (gap = 2, ≤ 4).
    # classify("subtraction", {"a": 13, "b": 11}) → primitive "sub.within20"
    _SLOT = {
        "role": "theme",
        "skill_id": "subtraction",
        "fact_key": "sub:13-11",
        "display": "13 − 11",
        "target_ms": 3000,
    }

    def _attempt(self, **overrides):
        base = {
            "position_in_session": 1,
            "skill_id": "subtraction",
            "parameters": {"a": 13, "b": 11},
            "expected_answer": 2,
            "correct": 0,
            "skipped": 0,
            "onset_latency_ms": None,
            "resolution_latency_ms": 3000,
        }
        base.update(overrides)
        return base

    def test_wrong_attempt_gets_tip(self):
        """A wrong (correct=0) attempt should receive a tip containing the expected answer."""
        attempt = self._attempt(correct=0)
        session_analysis.review_analysis([self._SLOT], [attempt])
        self.assertIn("tip", attempt)
        # The count-up tip for 13-11 mentions "2" (the expected answer).
        self.assertIn("2", attempt["tip"])

    def test_slow_correct_attempt_gets_tip(self):
        """A correct but slow primitive attempt (onset > 1200 × 1.25) should get a tip."""
        # onset_latency_ms=2000 > PRIMITIVE_ONSET_TARGET_MS(1200) × 1.25 = 1500
        attempt = self._attempt(correct=1, onset_latency_ms=2000)
        session_analysis.review_analysis([self._SLOT], [attempt])
        self.assertIn("tip", attempt)

    def test_fast_correct_attempt_has_no_tip(self):
        """A correct and fast attempt should NOT have a tip key."""
        # onset_latency_ms=800 < 1500 → not slow
        attempt = self._attempt(correct=1, onset_latency_ms=800)
        session_analysis.review_analysis([self._SLOT], [attempt])
        self.assertNotIn("tip", attempt)

    def test_skipped_attempt_has_no_tip(self):
        """Skipped attempts are never given a tip, regardless of latency."""
        attempt = self._attempt(skipped=1, correct=0)
        session_analysis.review_analysis([self._SLOT], [attempt])
        self.assertNotIn("tip", attempt)


if __name__ == "__main__":
    unittest.main()
