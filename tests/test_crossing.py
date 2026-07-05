"""Addition/subtraction problems must cross a multiple of 10.

A problem is valid only if a multiple of 10 lies strictly between the start
value and the result. Landing exactly on a ten does not count: 19-9=10 and
104+6=110 are invalid; 21-3=18 and 104+7=111 are valid.
"""

import unittest
from server import generator


class CrossesTenPredicateTests(unittest.TestCase):
    def test_landing_on_ten_is_not_crossing(self):
        self.assertFalse(generator._crosses_ten(19, 9, "-"))
        self.assertFalse(generator._crosses_ten(104, 6, "+"))

    def test_strictly_between_is_crossing(self):
        self.assertTrue(generator._crosses_ten(21, 3, "-"))
        self.assertTrue(generator._crosses_ten(104, 7, "+"))

    def test_same_band_is_not_crossing(self):
        self.assertFalse(generator._crosses_ten(15, 3, "+"))
        self.assertFalse(generator._crosses_ten(15, 3, "-"))

    def test_result_of_zero_is_not_crossing(self):
        self.assertFalse(generator._crosses_ten(7, 7, "-"))


class GeneratedProblemsCrossTenTests(unittest.TestCase):
    def _check(self, skill_id, op):
        for level in (1, 2, 3):
            for _ in range(200):
                p = generator.generate(skill_id, level=level)["parameters"]
                a, b = p["a"], p["b"]
                self.assertTrue(
                    generator._crosses_ten(a, b, op),
                    f"{skill_id} L{level}: {a} {op} {b} does not cross a ten",
                )
                if op == "-":
                    self.assertGreaterEqual(a - b, 0)

    def test_addition_crosses(self):
        self._check("addition", "+")

    def test_subtraction_crosses(self):
        self._check("subtraction", "-")


if __name__ == "__main__":
    unittest.main()
