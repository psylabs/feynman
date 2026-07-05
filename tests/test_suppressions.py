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

    def _pred(self, feature, **cmp):
        # single-comparator kwarg, e.g. self._pred("abs_diff", lte=2)
        _, fn = suppressions._compile_inline({"feature": feature, **cmp})
        return fn

    def test_trivial_diff_fires_on_features_not_skill(self):
        # abs_diff=1 → suppressed regardless of skill_id
        p = self._params(10, 9)
        fn = self._pred("abs_diff", lte=2)
        self.assertTrue(fn("addition", p))
        self.assertTrue(fn("weather_math", p))

    def test_trivial_diff_does_not_fire_on_large_diff(self):
        p = self._params(17, 2)  # abs_diff=15
        fn = self._pred("abs_diff", lte=2)
        self.assertFalse(fn("subtraction", p))

    def test_small_operand_new_rule(self):
        p = self._params(17, 2)  # min_operand=2
        fn = self._pred("min_operand", lte=2)
        self.assertTrue(fn("subtraction", p))

    def test_small_operand_does_not_fire_above_threshold(self):
        p = self._params(17, 3)  # min_operand=3
        fn = self._pred("min_operand", lte=2)
        self.assertFalse(fn("subtraction", p))

    def test_by_ten_skill_agnostic(self):
        p = self._params(18, 10)
        self.assertTrue(suppressions.REGISTRY["by_ten"]("subtraction", p))
        self.assertTrue(suppressions.REGISTRY["by_ten"]("addition", p))

    def test_subtract_zero_via_min_operand(self):
        p = self._params(5, 0)
        fn = self._pred("min_operand", eq=0)
        self.assertTrue(fn("subtraction", p))
        self.assertTrue(fn("addition", p))

    def test_generate_division_never_divides_by_small(self):
        # integration: division small_operand suppression blocks /1 and /2
        from server import suppressions as s
        s.load_active(force=True)
        for _ in range(50):
            # divisor 2 lives in the level-1 pool
            result = generator.generate("division", level=1)
            f = result["parameters"]["features"]
            self.assertGreater(f["min_operand"], 2, f"min_operand={f['min_operand']} should be suppressed")

    def test_generate_division_never_divides_by_ten(self):
        # regression (2026-07-02 user report): /10 kept appearing. Rule must
        # override scheduler targets too — a due FSRS item for 30/10 gets
        # re-sampled, not re-drilled.
        from server import suppressions as s
        s.load_active(force=True)
        for _ in range(20):
            result = generator.generate("division", target={"a": 30, "b": 10})
            self.assertNotEqual(result["parameters"]["b"], 10, "target /10 must be re-sampled")
        for lvl in (1, 2, 3):
            for _ in range(30):
                b = generator.generate("division", level=lvl)["parameters"]["b"]
                self.assertTrue(b > 2 and b % 10 != 0, f"level {lvl} emitted trivial divisor {b}")

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
    yaml_path.write_text("addition: [by_ten, trivial_value]\n")
    os.utime(yaml_path, (time.time() + 5, time.time() + 5))  # force mtime forward
    second = suppressions.load_active()
    assert second == {"addition": ["by_ten", "trivial_value"]}


class InlineComparatorTests(unittest.TestCase):
    """Unit tests for suppressions._compile_inline (the config-form entry
    compiler: {feature: <name>, <cmp>: <value>})."""

    def test_lte_fires_at_and_below_threshold(self):
        _, fn = suppressions._compile_inline({"feature": "abs_diff", "lte": 2})
        self.assertTrue(fn("x", {"features": {"abs_diff": 2}}))
        self.assertTrue(fn("x", {"features": {"abs_diff": 1}}))
        self.assertFalse(fn("x", {"features": {"abs_diff": 3}}))

    def test_gte_fires_at_and_above_threshold(self):
        _, fn = suppressions._compile_inline({"feature": "max_operand", "gte": 100})
        self.assertTrue(fn("x", {"features": {"max_operand": 100}}))
        self.assertTrue(fn("x", {"features": {"max_operand": 101}}))
        self.assertFalse(fn("x", {"features": {"max_operand": 99}}))

    def test_eq_fires_on_exact_match(self):
        _, fn = suppressions._compile_inline({"feature": "min_operand", "eq": 0})
        self.assertTrue(fn("x", {"features": {"min_operand": 0}}))
        self.assertFalse(fn("x", {"features": {"min_operand": 1}}))

    def test_in_fires_on_membership(self):
        _, fn = suppressions._compile_inline({"feature": "abs_diff", "in": [5, 10, 50]})
        self.assertTrue(fn("x", {"features": {"abs_diff": 10}}))
        self.assertFalse(fn("x", {"features": {"abs_diff": 7}}))

    def test_require_fires_when_feature_falsy(self):
        _, fn = suppressions._compile_inline({"feature": "crosses_ten", "require": True})
        self.assertTrue(fn("x", {"features": {"crosses_ten": False}}))
        self.assertFalse(fn("x", {"features": {"crosses_ten": True}}))

    def test_absent_feature_never_fires(self):
        entries = [
            {"feature": "abs_diff", "lte": 2},
            {"feature": "abs_diff", "gte": 0},
            {"feature": "abs_diff", "eq": 0},
            {"feature": "abs_diff", "in": [0, 1]},
            {"feature": "crosses_ten", "require": True},
        ]
        for entry in entries:
            _, fn = suppressions._compile_inline(entry)
            self.assertFalse(fn("x", {"features": {}}), entry)

    def test_malformed_entries_return_none(self):
        malformed = [
            {"feature": "abs_diff"},                       # no comparator
            {"feature": "abs_diff", "lte": 2, "gte": 1},    # two comparators
            {"lte": 2},                                     # no feature
            {"feature": "abs_diff", "bogus": 2},            # unknown comparator
            {"feature": "abs_diff", "lte": "two"},          # non-numeric lte
            {"feature": "abs_diff", "in": 5},                # in not a list
            {"feature": "crosses_ten", "require": False},   # require must be True
            {"feature": 5, "lte": 2},                        # feature not a str
        ]
        for entry in malformed:
            self.assertIsNone(suppressions._compile_inline(entry), entry)


def test_load_active_mixed_str_and_dict_entries(tmp_path, monkeypatch):
    yaml_path = tmp_path / "suppressions.yaml"
    yaml_path.write_text(
        "addition:\n"
        "  - by_ten\n"
        "  - {feature: abs_diff, lte: 2}\n"
    )
    monkeypatch.setattr(suppressions, "_YAML_PATH", yaml_path)
    suppressions._active_cache = None
    suppressions._cache_mtime = None
    active = suppressions.load_active()
    names = active["addition"]
    assert "by_ten" in names
    assert len(names) == 2
    inline_name = next(n for n in names if n != "by_ten")
    assert suppressions.REGISTRY[inline_name]("addition", {"features": {"abs_diff": 1}})
    assert not suppressions.REGISTRY[inline_name]("addition", {"features": {"abs_diff": 9}})


def test_load_active_ignores_malformed_dict_entry(tmp_path, monkeypatch):
    yaml_path = tmp_path / "suppressions.yaml"
    yaml_path.write_text(
        "addition:\n"
        "  - by_ten\n"
        "  - {feature: abs_diff, lte: 2, gte: 1}\n"  # malformed: two comparators
    )
    monkeypatch.setattr(suppressions, "_YAML_PATH", yaml_path)
    suppressions._active_cache = None
    suppressions._cache_mtime = None
    active = suppressions.load_active()
    assert active["addition"] == ["by_ten"]
