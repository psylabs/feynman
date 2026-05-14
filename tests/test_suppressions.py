import unittest
from server import generator

class FeatureTaggingTests(unittest.TestCase):
    def test_subtraction_params_include_features(self):
        result = generator.generate("subtraction")
        f = result["parameters"]["features"]
        self.assertIn("abs_diff", f)
        self.assertIn("min_operand", f)
        self.assertIn("max_operand", f)
        self.assertIn("has_borrow", f)
        a, b = result["parameters"]["a"], result["parameters"]["b"]
        self.assertEqual(f["abs_diff"], abs(a - b))
        self.assertEqual(f["min_operand"], min(a, b))
        self.assertEqual(f["max_operand"], max(a, b))

    def test_addition_params_include_features(self):
        result = generator.generate("addition")
        f = result["parameters"]["features"]
        self.assertIn("has_carry", f)

    def test_weather_delta_includes_features(self):
        # weather may hit network; use the fallback by forcing a known op
        from server import weather
        loc = {"name": "Test", "lat": 0.0, "lon": 0.0}
        forecast = weather._fallback_forecast()
        result = weather._wind_delta(loc, forecast)
        f = result["parameters"]["features"]
        self.assertIn("abs_diff", f)
        self.assertEqual(f["abs_diff"], abs(result["parameters"]["stronger"] - result["parameters"]["calmer"]))
        self.assertEqual(f["operation"], "wind_delta")
