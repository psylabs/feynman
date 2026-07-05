"""The bones-doctrine capstone contract test (Task 7).

For every skill in ``generator.GENERATORS`` x levels 1-3, a served problem's
``parameters`` must carry a ``features`` dict with ``min_operand``/
``max_operand`` (bones-doctrine: features describe every problem's bones,
not just addition/subtraction), and ``crosses_ten`` must be present whenever
the underlying bones op is "+"/"-" (movement or difference semantics) —
addition, subtraction, the weather deltas, and money's charge_total /
category_difference.

Plus the forge half of the contract: a synthetic pool file where a
non-crossing sub is skipped-and-marked-invalid and a crossing sub serves with
features stamped (server.forge_pool.take + server.forge_ops.features_for_entry
+ server.suppressions.matches, the same validator money.py/weather.py build).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import forge_ops, forge_pool, generator, money, suppressions, weather

# Synthetic transaction rows so money_arithmetic never touches the real CSV
# or live Plaid data. Amounts are picked so _swag() rounding still lands on
# non-round-ten values that clear the crosses_ten:require rule.
SYNTHETIC_ROWS = [
    {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries:Produce", "amount": 18.42},
    {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries:Produce", "amount": 33.01},
    {"date": "2026-01-15", "payee": "Trader Joe's", "category": "Groceries:Produce", "amount": 27.50},
    {"date": "2026-01-22", "payee": "Trader Joe's", "category": "Groceries:Produce", "amount": 41.75},
    {"date": "2026-01-05", "payee": "Con Edison", "category": "Utilities", "amount": 88.10},
    {"date": "2026-01-20", "payee": "Con Edison", "category": "Utilities", "amount": 102.30},
    {"date": "2026-01-06", "payee": "Ray's Diner", "category": "Dining & Drinks:Restaurants", "amount": 42.00},
    {"date": "2026-01-11", "payee": "Ray's Diner", "category": "Dining & Drinks:Restaurants", "amount": 68.00},
]

# Ops whose bones semantics are "+"/"-" (movement/difference) — crosses_ten
# must always be present for these.
_WEATHER_PLUS_MINUS_OPS = frozenset({"temp_delta", "daily_range", "wind_delta", "f_to_c_approx"})
_MONEY_PLUS_MINUS_OPS = frozenset({"charge_total", "category_difference"})


class BonesContractTests(unittest.TestCase):
    """Every GENERATORS skill x level 1-3 stamps a features contract."""

    def setUp(self):
        patches = [
            patch.object(forge_pool, "take", return_value=None),
            patch.object(weather, "load_forecast", return_value=weather._fallback_forecast()),
            patch.object(money, "load_transactions", return_value=SYNTHETIC_ROWS),
            patch.object(money, "load_recent_plaid_transactions", return_value=SYNTHETIC_ROWS),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _expects_crossing(self, skill_id: str, params: dict) -> bool:
        if skill_id in ("addition", "subtraction"):
            return True
        operation = params.get("operation")
        if skill_id == "weather_math":
            return operation in _WEATHER_PLUS_MINUS_OPS
        if skill_id == "money_arithmetic":
            return operation in _MONEY_PLUS_MINUS_OPS
        return False

    def test_every_skill_every_level_carries_features_contract(self):
        for skill_id in generator.GENERATORS:
            for level in (1, 2, 3):
                for i in range(40):
                    result = generator.generate(skill_id, level=level)
                    params = result.get("parameters", {})
                    with self.subTest(skill_id=skill_id, level=level, i=i,
                                       operation=params.get("operation")):
                        features = params.get("features")
                        self.assertIsInstance(
                            features, dict,
                            f"{skill_id}@L{level} missing features dict: {params}",
                        )
                        self.assertIn("min_operand", features)
                        self.assertIn("max_operand", features)
                        if self._expects_crossing(skill_id, params):
                            self.assertIn(
                                "crosses_ten", features,
                                f"{skill_id}@L{level} op={params.get('operation')} "
                                f"missing crosses_ten: {features}",
                            )

    def test_percent_of_stamps_features(self):
        """percent_of was a pre-existing gap (no features at all) — Task 7
        closes it: features come from bones "pct" over (percentage, base)."""
        result = generator.generate("percent_of", level=2)
        features = result["parameters"]["features"]
        self.assertEqual(features["min_operand"], min(result["parameters"]["percentage"],
                                                        result["parameters"]["base"]))
        self.assertEqual(features["max_operand"], max(result["parameters"]["percentage"],
                                                        result["parameters"]["base"]))
        self.assertNotIn("crosses_ten", features)  # "pct" has no crossing key


class ForgeFeaturesContractTests(unittest.TestCase):
    """The forge half of the contract: invalid entries skipped-and-marked,
    valid entries serve with features stamped (server/forge_pool.py +
    server/forge_ops.py, the same validator money.py/weather.py build)."""

    def _entry(self, id_, args, op="sub", operation="category_difference",
               skill_id="money_arithmetic"):
        return {
            "id": id_,
            "prompt": f"placeholder prompt for {args}",
            "skill_id": skill_id,
            "operation": operation,
            "op": op,
            "args": list(args),
            "source": "test",
            "created_at": 0.0,
            "used": False,
        }

    def _validator(self, skill_id: str):
        active = suppressions.load_active()

        def _valid(entry: dict) -> bool:
            features = forge_ops.features_for_entry(entry)
            return features is not None and not suppressions.matches(
                skill_id, {"features": features}, active,
            )

        return _valid

    def test_invalid_entry_skipped_and_valid_entry_serves_with_features(self):
        # (15, 3): movement default crosses_ten(15, 12) is False (lands in
        # the same decade after the -1 demotion) — must be skipped-and-marked.
        invalid = self._entry("invalid01", (15, 3))
        # (21, 3): crosses_ten(21, 18) is True — must serve with features.
        valid = self._entry("valid01", (21, 3))

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"
            pool_path.write_text(json.dumps([invalid, valid]))
            with patch.object(forge_pool, "POOL_PATH", pool_path):
                result = forge_pool.take(
                    "money_arithmetic", "category_difference",
                    validator=self._validator("money_arithmetic"),
                )
                on_disk = json.loads(pool_path.read_text())

        # Invalid entry: skipped, marked used + invalid, scan continued.
        self.assertTrue(on_disk[0]["used"])
        self.assertTrue(on_disk[0].get("invalid"))

        # Valid entry: served, marked used (not invalid).
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "valid01")
        self.assertTrue(on_disk[1]["used"])
        self.assertNotIn("invalid", on_disk[1])

        # Features stamped from the served entry carry the full contract.
        features = forge_ops.features_for_entry(result)
        self.assertEqual(features["min_operand"], 3)
        self.assertEqual(features["max_operand"], 21)
        self.assertTrue(features["crosses_ten"])


if __name__ == "__main__":
    unittest.main()
