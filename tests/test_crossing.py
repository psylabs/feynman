"""Addition/subtraction problems must cross a multiple of 10.

A problem is valid only if a multiple of 10 lies strictly between the
ENDPOINTS of the number-line segment the mental computation traverses.
Landing exactly on a ten does not count: 19-9=10 and 104+6=110 are invalid;
21-3=18 and 104+7=111 are valid. See ``server.bones.crosses_ten`` for the
endpoint predicate itself; this module exercises the generator's use of it.
"""

import unittest
from server import bones, generator, suppressions


class CrossesTenPredicateTests(unittest.TestCase):
    """Sanity-check bones.crosses_ten against the endpoints each op
    traverses (start value, result) — same cases the old 3-arg
    generator._crosses_ten(a, b, op) covered, ported to the 2-arg endpoint
    signature."""

    def test_landing_on_ten_is_not_crossing(self):
        self.assertFalse(bones.crosses_ten(19, 19 - 9))  # 19-9=10
        self.assertFalse(bones.crosses_ten(104, 104 + 6))  # 104+6=110

    def test_strictly_between_is_crossing(self):
        self.assertTrue(bones.crosses_ten(21, 21 - 3))  # 21-3=18
        self.assertTrue(bones.crosses_ten(104, 104 + 7))  # 104+7=111

    def test_same_band_is_not_crossing(self):
        self.assertFalse(bones.crosses_ten(15, 15 + 3))
        self.assertFalse(bones.crosses_ten(15, 15 - 3))

    def test_result_of_zero_is_not_crossing(self):
        self.assertFalse(bones.crosses_ten(7, 7 - 7))


class GeneratedProblemsCrossTenTests(unittest.TestCase):
    def test_addition_crosses(self):
        for level in (1, 2, 3):
            for _ in range(200):
                p = generator.generate("addition", level=level)["parameters"]
                a, b = p["a"], p["b"]
                self.assertTrue(
                    bones.crosses_ten(a, a + b),
                    f"addition L{level}: {a} + {b} does not cross a ten",
                )

    def test_subtraction_crosses(self):
        for level in (1, 2, 3):
            for _ in range(200):
                p = generator.generate("subtraction", level=level)["parameters"]
                a, b = p["a"], p["b"]
                self.assertTrue(
                    bones.crosses_ten(a, a - b),
                    f"subtraction L{level}: {a} - {b} does not cross a ten",
                )
                self.assertGreaterEqual(a - b, 0)


class FallbackPairsSurviveSuppressionTests(unittest.TestCase):
    """Give-up fallback pairs (returned when a sampler's rejection loop
    exhausts its retries) must themselves pass every active rule for their
    skill. Otherwise a suppression edit could silently turn the give-up path
    into a trivial-problem generator with no test to catch it."""

    def test_addition_fallbacks_pass_active_rules(self):
        active = suppressions.load_active()
        for a, b in [(9, 9), (9, 16), (17, 19)]:
            params = {"a": a, "b": b, "features": bones.compute_features("+", (a, b))}
            self.assertIsNone(
                suppressions.matches("addition", params, active),
                f"addition fallback ({a}, {b}) is suppressed",
            )

    def test_subtraction_fallbacks_pass_active_rules(self):
        active = suppressions.load_active()
        for a, b in [(12, 5), (15, 8), (25, 12)]:
            params = {"a": a, "b": b, "features": bones.compute_features("-", (a, b))}
            self.assertIsNone(
                suppressions.matches("subtraction", params, active),
                f"subtraction fallback ({a}, {b}) is suppressed",
            )


if __name__ == "__main__":
    unittest.main()
