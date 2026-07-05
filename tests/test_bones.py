"""Tests for server.bones: the crosses_ten predicate and compute_features."""

import unittest

from server import bones


class CrossesTenTests(unittest.TestCase):
    """Exact cases from the bones-doctrine plan (Task 1)."""

    def test_landing_exactly_on_ten_is_not_crossing(self):
        self.assertFalse(bones.crosses_ten(19, 10))  # 19-9 lands on ten
        self.assertFalse(bones.crosses_ten(104, 110))

    def test_strictly_between_is_crossing(self):
        self.assertTrue(bones.crosses_ten(21, 18))
        self.assertTrue(bones.crosses_ten(104, 111))

    def test_same_decade_after_stripping_trailing_zeros_is_not_crossing(self):
        self.assertFalse(bones.crosses_ten(190, 100))
        self.assertFalse(bones.crosses_ten(1040, 1100))

    def test_crossing_survives_trailing_zero_reduction(self):
        self.assertTrue(bones.crosses_ten(1039, 1098))

    def test_same_decade_no_trailing_zeros(self):
        self.assertFalse(bones.crosses_ten(68, 62))

    def test_crossing_no_trailing_zeros(self):
        self.assertTrue(bones.crosses_ten(71, 58))

    def test_reduces_common_trailing_zeros_before_judging(self):
        self.assertTrue(bones.crosses_ten(2400, 1500))  # reduces to (24, 15)

    def test_reduction_can_flip_to_not_crossing(self):
        self.assertFalse(bones.crosses_ten(200, 500))  # 200+300

    def test_equal_endpoints_is_not_crossing_even_at_a_ten(self):
        self.assertFalse(bones.crosses_ten(20, 20))

    def test_zero_endpoint_defers_to_the_other(self):
        self.assertFalse(bones.crosses_ten(7, 0))
        self.assertFalse(bones.crosses_ten(90, 0))
        self.assertFalse(bones.crosses_ten(100, 90))

    def test_intermediate_tens_count_even_when_result_lands_on_a_ten(self):
        self.assertTrue(bones.crosses_ten(105, 200))


class ComputeFeaturesAlwaysOnTests(unittest.TestCase):
    def test_min_max_operand_present_for_every_op(self):
        for op, operands in (
            ("+", (16, 22)),
            ("-", (21, 3)),
            ("*", (7, 13)),
            ("/", (7, 13)),
            ("pct", (15, 200)),
        ):
            f = bones.compute_features(op, operands)
            self.assertEqual(f["min_operand"], min(operands), op)
            self.assertEqual(f["max_operand"], max(operands), op)

    def test_abs_diff_present_only_for_exactly_two_operands(self):
        f = bones.compute_features("-", (21, 3))
        self.assertEqual(f["abs_diff"], 18)

        f = bones.compute_features("+", (17, 24, 15))
        self.assertNotIn("abs_diff", f)

    def test_extra_merged_last_and_can_override(self):
        f = bones.compute_features("+", (16, 22), extra={"operation": "charge_total"})
        self.assertEqual(f["operation"], "charge_total")

        f = bones.compute_features(
            "+", (16, 22), extra={"min_operand": "overridden"}
        )
        self.assertEqual(f["min_operand"], "overridden")


class ComputeFeaturesAdditionTests(unittest.TestCase):
    def test_movement_default_endpoints_two_operands(self):
        # No endpoints given: defaults to crosses_ten(run, run + x) for the
        # single step, i.e. crosses_ten(16, 38) -> True (16 and 38 are two
        # decades apart).
        f = bones.compute_features("+", (16, 22))
        self.assertTrue(f["crosses_ten"])
        self.assertFalse(f["has_carry"])  # 6 + 2 = 8 < 10

    def test_explicit_endpoints_override_running_sum(self):
        # Movement default over (53, 20) would cross (crosses_ten(53, 73)
        # -> True), but explicit endpoints in the same decade band force
        # the override to win.
        f_default = bones.compute_features("+", (53, 20))
        self.assertTrue(f_default["crosses_ten"])
        f = bones.compute_features("+", (53, 20), endpoints=(24, 22))
        self.assertFalse(f["crosses_ten"])

    def test_multi_operand_every_step_crosses_is_true(self):
        # [17, 24, 15]: step1 crosses_ten(17, 41)=True, step2
        # crosses_ten(41, 56)=True -> every step crosses -> True.
        f = bones.compute_features("+", (17, 24, 15))
        self.assertTrue(f["crosses_ten"])
        self.assertTrue(f["has_carry"])  # step1: 7 + 4 = 11 >= 10

    def test_multi_operand_one_step_not_crossing_is_false(self):
        # (12, 5, 30): step1 crosses_ten(12, 17) -> False (same decade,
        # 12 and 17 both band 1) so the "every step" requirement fails even
        # though step2 (17 -> 47) does cross.
        f = bones.compute_features("+", (12, 5, 30))
        self.assertFalse(bones.crosses_ten(12, 17))
        self.assertTrue(bones.crosses_ten(17, 47))
        self.assertFalse(f["crosses_ten"])

    def test_carry_detected_across_later_running_sum_step(self):
        # (16, 22, 16): step1 has no ones-digit carry (6 + 2 = 8), but
        # step2 does (8 + 6 = 14 >= 10). has_carry is True because ANY
        # step carries, not just the first.
        #
        # Note: the source plan's worked example claims this same operand
        # list yields crosses_ten == False ("16+22=38 fails"). That is not
        # reproducible under the stated algorithm: crosses_ten(16, 38) and
        # crosses_ten(38, 54) both independently verify True (each step
        # spans a full decade boundary), so the correct crosses_ten value
        # for this list is True. This test only asserts the has_carry
        # behavior; see test_multi_operand_one_step_not_crossing_is_false
        # above for a verified not-every-step-crosses example.
        f = bones.compute_features("+", (16, 22, 16))
        self.assertTrue(f["has_carry"])
        self.assertTrue(f["crosses_ten"])

    def test_no_carry_across_any_step(self):
        f = bones.compute_features("+", (10, 5, 3))
        self.assertFalse(f["has_carry"])

    def test_addition_has_no_borrow_key(self):
        f = bones.compute_features("+", (16, 22))
        self.assertNotIn("has_borrow", f)


class ComputeFeaturesSubtractionTests(unittest.TestCase):
    def test_movement_default_endpoints(self):
        # crosses_ten(21, 21 - 3) == crosses_ten(21, 18) -> True (matches
        # the (21, 18) True case from the crosses_ten table).
        f = bones.compute_features("-", (21, 3))
        self.assertTrue(f["crosses_ten"])
        self.assertTrue(f["has_borrow"])  # (1 % 10)=1 < (3 % 10)=3

    def test_has_borrow_true_when_ones_digit_smaller(self):
        f = bones.compute_features("-", (32, 5))
        self.assertTrue(f["has_borrow"])  # 2 < 5

    def test_has_borrow_false_when_ones_digit_not_smaller(self):
        f = bones.compute_features("-", (37, 5))
        self.assertFalse(f["has_borrow"])  # 7 >= 5

    def test_difference_endpoints_override_movement_default(self):
        # Movement default over (53, 23) crosses (crosses_ten(53, 30) ->
        # True). Difference endpoints (24, 22) sit in the same decade band
        # and override to False.
        f = bones.compute_features("-", (53, 23), endpoints=(24, 22))
        self.assertFalse(f["crosses_ten"])
        # movement default (no endpoints) would have been True for the
        # same operands, proving the override actually took effect.
        f_default = bones.compute_features("-", (53, 23))
        self.assertTrue(f_default["crosses_ten"])

    def test_subtraction_has_no_carry_key(self):
        f = bones.compute_features("-", (21, 3))
        self.assertNotIn("has_carry", f)


class ComputeFeaturesNonCrossingOpsTests(unittest.TestCase):
    def test_multiplication_has_no_carry_borrow_or_crossing_keys(self):
        f = bones.compute_features("*", (7, 13))
        for key in ("has_carry", "has_borrow", "crosses_ten"):
            self.assertNotIn(key, f)
        self.assertEqual(f["abs_diff"], 6)

    def test_division_uses_divisor_quotient_convention(self):
        # Caller passes (divisor, quotient), not (dividend, divisor).
        divisor, quotient = 7, 13
        f = bones.compute_features("/", (divisor, quotient))
        self.assertEqual(f["min_operand"], 7)
        self.assertEqual(f["max_operand"], 13)
        self.assertEqual(f["abs_diff"], 6)
        for key in ("has_carry", "has_borrow", "crosses_ten"):
            self.assertNotIn(key, f)

    def test_pct_has_no_carry_borrow_or_crossing_keys(self):
        f = bones.compute_features("pct", (15, 200))
        for key in ("has_carry", "has_borrow", "crosses_ten"):
            self.assertNotIn(key, f)
        self.assertEqual(f["min_operand"], 15)
        self.assertEqual(f["max_operand"], 200)


if __name__ == "__main__":
    unittest.main()
