import os
import time
import unittest
from server import generator
from server import suppressions

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


class SuppressionRuleTests(unittest.TestCase):
    def _params(self, a, b, **extra):
        from server.generator import _compute_features
        return {"a": a, "b": b, "features": _compute_features(a, b, extra=extra or None)}

    def test_trivial_diff_fires_on_features_not_skill(self):
        # abs_diff=1 → suppressed regardless of skill_id
        p = self._params(10, 9)
        self.assertIsNotNone(suppressions.REGISTRY["trivial_diff"]("addition", p))
        self.assertIsNotNone(suppressions.REGISTRY["trivial_diff"]("weather_math", p))

    def test_trivial_diff_does_not_fire_on_large_diff(self):
        p = self._params(17, 2)  # abs_diff=15
        self.assertFalse(suppressions.REGISTRY["trivial_diff"]("subtraction", p))

    def test_small_operand_new_rule(self):
        p = self._params(17, 2)  # min_operand=2
        self.assertTrue(suppressions.REGISTRY["small_operand"]("subtraction", p))

    def test_small_operand_does_not_fire_above_threshold(self):
        p = self._params(17, 3)  # min_operand=3
        self.assertFalse(suppressions.REGISTRY["small_operand"]("subtraction", p))

    def test_by_ten_skill_agnostic(self):
        p = self._params(18, 10)
        self.assertTrue(suppressions.REGISTRY["by_ten"]("subtraction", p))
        self.assertTrue(suppressions.REGISTRY["by_ten"]("addition", p))

    def test_subtract_zero_via_min_operand(self):
        p = self._params(5, 0)
        self.assertTrue(suppressions.REGISTRY["subtract_zero"]("subtraction", p))
        self.assertTrue(suppressions.REGISTRY["subtract_zero"]("addition", p))

    def test_generate_subtraction_never_returns_small_b(self):
        # integration: active suppressions prevent b<=2 from being generated
        from server import suppressions as s
        s.load_active(force=True)
        for _ in range(50):
            result = generator.generate("subtraction")
            b = result["parameters"]["b"]
            self.assertGreater(b, 2, f"b={b} should be suppressed")


def test_load_active_reloads_on_mtime_change(tmp_path, monkeypatch):
    yaml_path = tmp_path / "suppressions.yaml"
    yaml_path.write_text("addition: [by_ten]\n")
    monkeypatch.setattr(suppressions, "_YAML_PATH", yaml_path)
    suppressions._active_cache = None  # reset module cache
    suppressions._cache_mtime = None   # reset mtime cache
    first = suppressions.load_active()
    assert first == {"addition": ["by_ten"]}
    yaml_path.write_text("addition: [by_ten, small_operand]\n")
    os.utime(yaml_path, (time.time() + 5, time.time() + 5))  # force mtime forward
    second = suppressions.load_active()
    assert second == {"addition": ["by_ten", "small_operand"]}
