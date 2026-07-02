"""Tests for server/forge_pool.py and the forge-pool integration in money.py/weather.py."""
from __future__ import annotations

import json
import logging
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

from server import forge_pool, money, weather
from server.taxonomy import classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    *,
    id: str = "abc123def456",
    prompt: str = "You spent $120. What is 15 percent of that?",
    skill_id: str = "money_arithmetic",
    operation: str = "charge_total",
    op: str = "pct_of",
    args: list = None,
    source: str = "plaid.latest.json",
    used: bool = False,
) -> dict:
    return {
        "id": id,
        "prompt": prompt,
        "skill_id": skill_id,
        "operation": operation,
        "op": op,
        "args": args if args is not None else [15, 120],
        "source": source,
        "created_at": 1_700_000_000.0,
        "used": used,
    }


# ---------------------------------------------------------------------------
# forge_pool.take() unit tests
# ---------------------------------------------------------------------------


class TestTake(unittest.TestCase):
    def _write_pool(self, tmp_dir: Path, entries: list) -> Path:
        p = tmp_dir / "pool.json"
        p.write_text(json.dumps(entries, indent=2))
        return p

    def test_returns_none_when_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nonexistent.json"
            with patch.object(forge_pool, "POOL_PATH", missing):
                result = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNone(result)

    def test_returns_none_when_file_not_json(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pool.json"
            bad.write_text("not json {{")
            with patch.object(forge_pool, "POOL_PATH", bad):
                result = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNone(result)

    def test_returns_none_when_no_matching_unused_entry(self):
        entry = _make_entry(operation="charge_total", used=True)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNone(result)

    def test_returns_none_when_operation_mismatch(self):
        entry = _make_entry(operation="charge_total", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = forge_pool.take("money_arithmetic", "category_difference")
        self.assertIsNone(result)

    def test_returns_none_when_skill_id_mismatch(self):
        entry = _make_entry(skill_id="weather_math", operation="temp_delta", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = forge_pool.take("money_arithmetic", "temp_delta")
        self.assertIsNone(result)

    def test_returns_matching_entry(self):
        entry = _make_entry(operation="charge_total", skill_id="money_arithmetic", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], entry["id"])
        self.assertEqual(result["prompt"], entry["prompt"])

    def test_marks_entry_used_in_file(self):
        entry = _make_entry(operation="charge_total", skill_id="money_arithmetic", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                forge_pool.take("money_arithmetic", "charge_total")
                # Read file back and check used=True
                on_disk = json.loads(pool.read_text())
        self.assertTrue(on_disk[0]["used"])

    def test_consumed_only_once(self):
        entry = _make_entry(operation="charge_total", skill_id="money_arithmetic", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                first = forge_pool.take("money_arithmetic", "charge_total")
                second = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_skips_used_entries_and_returns_next_unused(self):
        used_entry = _make_entry(id="used111", operation="charge_total", used=True)
        fresh_entry = _make_entry(id="fresh222", operation="charge_total", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [used_entry, fresh_entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = forge_pool.take("money_arithmetic", "charge_total")
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], "fresh222")

    def test_write_is_atomic(self):
        """After take(), the pool file exists and is valid JSON (no half-writes)."""
        entry = _make_entry(operation="charge_total", used=False)
        with tempfile.TemporaryDirectory() as td:
            pool = self._write_pool(Path(td), [entry])
            with patch.object(forge_pool, "POOL_PATH", pool):
                forge_pool.take("money_arithmetic", "charge_total")
            content = json.loads(pool.read_text())
        self.assertIsInstance(content, list)


# ---------------------------------------------------------------------------
# money.py forge integration
# ---------------------------------------------------------------------------


class TestMoneyForgeIntegration(unittest.TestCase):
    """generate_problem() draws from the pool, then falls back to built-in."""

    def _pool_entry(self) -> dict:
        return _make_entry(
            id="moneyentry01",
            prompt="You had 2 charges at Grocery: $30 and $20. About what's the total?",
            skill_id="money_arithmetic",
            operation="charge_total",
            op="add_list",
            args=[30, 20],
            used=False,
        )

    def test_forge_entry_consumed_when_available(self):
        entry = self._pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(target={"operation": "charge_total"})

        self.assertEqual(result["prompt"], entry["prompt"])
        self.assertEqual(result["expected"], 50.0)  # add_list(30, 20) = 50
        self.assertEqual(result["parameters"]["source"], "forge")
        self.assertEqual(result["parameters"]["forge_id"], "moneyentry01")
        self.assertEqual(result["parameters"]["operation"], "charge_total")

    def test_forge_entry_consumed_only_once_then_builtin(self):
        entry = self._pool_entry()
        # Provide CSV rows so the built-in template can produce a real problem.
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                first = money.generate_problem(rows=rows, target={"operation": "charge_total"})
                second = money.generate_problem(rows=rows, target={"operation": "charge_total"})

        self.assertEqual(first["parameters"]["source"], "forge")
        self.assertNotEqual(second["parameters"]["source"], "forge")

    def test_missing_pool_falls_through_to_builtin(self):
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no_pool.json"
            with patch.object(forge_pool, "POOL_PATH", missing):
                result = money.generate_problem(rows=rows, target={"operation": "charge_total"})

        self.assertNotEqual(result["parameters"].get("source"), "forge")

    def test_malformed_entry_skipped_and_marked_used(self):
        """Bad op/args: entry gets marked used and generation falls through to built-in."""
        bad_entry = _make_entry(
            id="badentry001",
            operation="charge_total",
            op="div",    # div(10, 0) → ZeroDivisionError
            args=[10, 0],
            used=False,
        )
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([bad_entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                with self.assertLogs("server.money", level="WARNING") as log_ctx:
                    result = money.generate_problem(rows=rows, target={"operation": "charge_total"})
                on_disk = json.loads(pool.read_text())

        # Should not crash; should fall through to built-in
        self.assertNotEqual(result["parameters"].get("source"), "forge")
        # Entry should be marked used on disk
        self.assertTrue(on_disk[0]["used"])
        # Warning should have been logged
        self.assertTrue(any("malformed" in m.lower() or "badentry001" in m for m in log_ctx.output))

    def test_forge_parameters_include_level(self):
        entry = self._pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(
                    target={"operation": "charge_total", "level": "advanced"}
                )

        self.assertEqual(result["parameters"]["level"], "advanced")

    def test_forge_parameters_level_none_when_absent(self):
        entry = self._pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(target={"operation": "charge_total"})

        self.assertIsNone(result["parameters"]["level"])


# ---------------------------------------------------------------------------
# weather.py forge integration
# ---------------------------------------------------------------------------


class TestWeatherForgeIntegration(unittest.TestCase):
    def _pool_entry(self) -> dict:
        return _make_entry(
            id="weatherent01",
            prompt="Today's high is 72 and low is 55. What's the daily range?",
            skill_id="weather_math",
            operation="daily_range",
            op="delta",
            args=[72, 55],
            source="open-meteo",
            used=False,
        )

    def test_forge_entry_consumed_when_available(self):
        entry = self._pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = weather.generate_problem(target={"operation": "daily_range"})

        self.assertEqual(result["prompt"], entry["prompt"])
        self.assertEqual(result["expected"], 17.0)  # abs(72 - 55) = 17
        self.assertEqual(result["parameters"]["source"], "forge")
        self.assertEqual(result["parameters"]["forge_id"], "weatherent01")
        self.assertEqual(result["parameters"]["operation"], "daily_range")

    def test_forge_entry_consumed_only_once_then_builtin(self):
        entry = self._pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                first = weather.generate_problem(target={"operation": "daily_range"})
                second = weather.generate_problem(target={"operation": "daily_range"})

        self.assertEqual(first["parameters"]["source"], "forge")
        self.assertNotEqual(second["parameters"]["source"], "forge")

    def test_missing_pool_falls_through_to_builtin(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "no_pool.json"
            with patch.object(forge_pool, "POOL_PATH", missing):
                result = weather.generate_problem(target={"operation": "daily_range"})

        self.assertNotEqual(result["parameters"].get("source"), "forge")

    def test_malformed_weather_entry_skipped_marked_used(self):
        """Bad op/args: entry gets marked used, generation falls through."""
        bad_entry = _make_entry(
            id="wbadentry01",
            prompt="How much colder? What's the delta?",
            skill_id="weather_math",
            operation="daily_range",
            op="delta",
            args=["not_a_number", 55],  # TypeError from arithmetic
            used=False,
        )
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([bad_entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                with self.assertLogs("server.weather", level="WARNING") as log_ctx:
                    result = weather.generate_problem(target={"operation": "daily_range"})
                on_disk = json.loads(pool.read_text())

        self.assertNotEqual(result["parameters"].get("source"), "forge")
        self.assertTrue(on_disk[0]["used"])
        self.assertTrue(any("malformed" in m.lower() or "wbadentry01" in m for m in log_ctx.output))


# ---------------------------------------------------------------------------
# forge_pool.eligible() unit tests
# ---------------------------------------------------------------------------


class TestEligible(unittest.TestCase):
    """eligible() returns False when target contains keys beyond operation+level."""

    def test_none_is_eligible(self):
        self.assertTrue(forge_pool.eligible(None))

    def test_empty_dict_is_eligible(self):
        self.assertTrue(forge_pool.eligible({}))

    def test_operation_only_is_eligible(self):
        self.assertTrue(forge_pool.eligible({"operation": "charge_total"}))

    def test_operation_and_level_is_eligible(self):
        self.assertTrue(forge_pool.eligible({"operation": "charge_total", "level": 2}))

    def test_extra_key_people_not_eligible(self):
        self.assertFalse(forge_pool.eligible({"operation": "charge_total", "people": 4}))

    def test_extra_key_location_not_eligible(self):
        self.assertFalse(forge_pool.eligible({"operation": "temp_delta", "location": "NYC"}))

    def test_extra_key_charge_not_eligible(self):
        self.assertFalse(forge_pool.eligible({"operation": "restaurant_tip_15", "charge": {"payee": "Diner"}}))


# ---------------------------------------------------------------------------
# Fix 1: live-Plaid ops never consumed from pool
# ---------------------------------------------------------------------------


class TestMoneyPlaidOpsExcluded(unittest.TestCase):
    """restaurant_tip_15 and split_bill must NEVER be served from the forge pool."""

    def _pool_entry(self, operation: str) -> dict:
        return _make_entry(
            id="rtp15entry01",
            prompt="You spent $120 at Diner. What is a 15 percent tip?",
            skill_id="money_arithmetic",
            operation=operation,
            op="pct_of",
            args=[15, 120],
            used=False,
        )

    def test_restaurant_tip_15_not_consumed_from_pool(self):
        """Pool entry for restaurant_tip_15 is ignored; built-in Plaid path fires."""
        entry = self._pool_entry("restaurant_tip_15")
        rows = [{"date": "2026-01-02", "payee": "Diner", "category": "Dining", "amount": 80.0}]
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(rows=rows, target={"operation": "restaurant_tip_15"})
                on_disk = json.loads(pool.read_text())

        # Entry must NOT be consumed (used stays False)
        self.assertFalse(on_disk[0]["used"])
        # Built-in Plaid path fires — source is not "forge"
        self.assertNotEqual(result["parameters"].get("source"), "forge")
        # It's a real tip problem
        self.assertEqual(result["parameters"]["operation"], "restaurant_tip_15")

    def test_split_bill_not_consumed_from_pool(self):
        """Pool entry for split_bill is ignored; built-in path fires."""
        entry = self._pool_entry("split_bill")
        rows = [{"date": "2026-01-02", "payee": "Diner", "category": "Dining", "amount": 84.0}]
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(rows=rows, target={"operation": "split_bill"})
                on_disk = json.loads(pool.read_text())

        self.assertFalse(on_disk[0]["used"])
        self.assertNotEqual(result["parameters"].get("source"), "forge")
        self.assertEqual(result["parameters"]["operation"], "split_bill")


# ---------------------------------------------------------------------------
# Fix 2: pinned targets skip the pool (money + weather)
# ---------------------------------------------------------------------------


class TestPinnedTargetSkipsPool(unittest.TestCase):
    """target with extra keys must not consume a pool entry in either module."""

    def _money_pool_entry(self) -> dict:
        return _make_entry(
            id="money_pinned01",
            prompt="You had 2 charges at Grocery: $30 and $20. About what's the total?",
            skill_id="money_arithmetic",
            operation="charge_total",
            op="add_list",
            args=[30, 20],
            used=False,
        )

    def _weather_pool_entry(self) -> dict:
        return _make_entry(
            id="weather_pinned01",
            prompt="Today's high is 72 and low is 55. What's the daily range?",
            skill_id="weather_math",
            operation="daily_range",
            op="delta",
            args=[72, 55],
            source="open-meteo",
            used=False,
        )

    def test_money_pinned_target_does_not_consume_pool(self):
        """target={"operation": "charge_total", "people": 4} skips the pool."""
        entry = self._money_pool_entry()
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(
                    rows=rows,
                    target={"operation": "charge_total", "people": 4},
                )
                on_disk = json.loads(pool.read_text())

        self.assertFalse(on_disk[0]["used"])
        self.assertNotEqual(result["parameters"].get("source"), "forge")

    def test_money_operation_and_level_target_consumes_pool(self):
        """target={"operation": "charge_total", "level": 2} IS eligible — pool entry consumed."""
        entry = self._money_pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = money.generate_problem(
                    target={"operation": "charge_total", "level": 2},
                )
                on_disk = json.loads(pool.read_text())

        self.assertTrue(on_disk[0]["used"])
        self.assertEqual(result["parameters"].get("source"), "forge")

    def test_weather_pinned_target_does_not_consume_pool(self):
        """weather: target with extra key (e.g., location) skips the pool."""
        entry = self._weather_pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = weather.generate_problem(
                    target={"operation": "daily_range", "location": "NYC"},
                )
                on_disk = json.loads(pool.read_text())

        self.assertFalse(on_disk[0]["used"])
        self.assertNotEqual(result["parameters"].get("source"), "forge")

    def test_weather_operation_and_level_target_consumes_pool(self):
        """weather: target={"operation": "daily_range", "level": 2} consumes the pool."""
        entry = self._weather_pool_entry()
        with tempfile.TemporaryDirectory() as td:
            pool = Path(td) / "pool.json"
            pool.write_text(json.dumps([entry]))
            with patch.object(forge_pool, "POOL_PATH", pool):
                result = weather.generate_problem(
                    target={"operation": "daily_range", "level": 2},
                )
                on_disk = json.loads(pool.read_text())

        self.assertTrue(on_disk[0]["used"])
        self.assertEqual(result["parameters"].get("source"), "forge")


# ---------------------------------------------------------------------------
# taxonomy.classify works with forge parameters
# ---------------------------------------------------------------------------


class TestForgeClassify(unittest.TestCase):
    def test_classify_money_forge_returns_money_key(self):
        params = {
            "operation": "charge_total",
            "source": "forge",
            "forge_id": "abc123",
            "level": None,
        }
        taxon = classify("money_arithmetic", params)
        self.assertIsNotNone(taxon)
        self.assertEqual(taxon.key, "money:charge_total")

    def test_classify_weather_forge_returns_weather_key(self):
        params = {
            "operation": "daily_range",
            "source": "forge",
            "forge_id": "def456",
            "level": None,
        }
        taxon = classify("weather_math", params)
        self.assertIsNotNone(taxon)
        self.assertEqual(taxon.key, "weather:daily_range")

    def test_classify_money_forge_restaurant_tip_15(self):
        params = {
            "operation": "restaurant_tip_15",
            "source": "forge",
            "forge_id": "tip001",
            "level": None,
        }
        taxon = classify("money_arithmetic", params)
        self.assertIsNotNone(taxon)
        self.assertEqual(taxon.key, "money:restaurant_tip_15")


if __name__ == "__main__":
    unittest.main()
