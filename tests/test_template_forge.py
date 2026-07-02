"""Tests for tools/template_forge.py — LLM is always mocked."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import tools.template_forge as forge
from tools.template_forge import run, _validate, _candidate_id, main

# ---------------------------------------------------------------------------
# Canned candidates
# ---------------------------------------------------------------------------
# 1. Valid: op in OPS, args compute, numbers in prompt are in args, valid
#    operation, prompt ≤180 chars ends with ?, not duplicate.
VALID = {
    "prompt": "You spent $120 at Chipotle. What is 15 percent of that?",
    "skill_id": "money_arithmetic",
    "operation": "restaurant_tip_15",
    "op": "pct_of",
    "args": [15, 120],
    "source": "plaid.latest.json",
}

# 2. Hallucinated number: 99 appears in the prompt but is not in args.
HALLUCINATED = {
    "prompt": "You had 99 extra charges but spent $120 at lunch. What's 15 percent?",
    "skill_id": "money_arithmetic",
    "operation": "restaurant_tip_15",
    "op": "pct_of",
    "args": [15, 120],
    "source": "plaid.latest.json",
}

# 3. Unknown op: "multiply" is not in forge_ops.OPS.
UNKNOWN_OP = {
    "prompt": "You spent $50 at Starbucks. What is 20 percent tip?",
    "skill_id": "money_arithmetic",
    "operation": "restaurant_tip_15",
    "op": "multiply",
    "args": [50, 0.20],
    "source": "plaid.latest.json",
}

# 4. Duplicate: same case-folded prompt as an entry already in the pool.
DUPLICATE = {
    "prompt": "Today's high is 72 and low is 55. What's the daily range?",
    "skill_id": "weather_math",
    "operation": "daily_range",
    "op": "delta",
    "args": [72, 55],
    "source": "open-meteo",
}

# Pre-existing pool entry with same prompt (different case)
EXISTING_POOL_ENTRY = {
    "id": _candidate_id(DUPLICATE["prompt"]),
    "prompt": DUPLICATE["prompt"].upper(),   # different case, same casefold
    "skill_id": "weather_math",
    "operation": "daily_range",
    "op": "delta",
    "args": [72, 55],
    "source": "open-meteo",
    "created_at": 1_700_000_000.0,
    "used": False,
}

CANNED = [VALID, HALLUCINATED, UNKNOWN_OP, DUPLICATE]

_FAKE_CONTEXT: dict = {}


class TestValidate(unittest.TestCase):
    """Unit tests for _validate — no LLM involved."""

    def _existing(self):
        return {EXISTING_POOL_ENTRY["prompt"].casefold()}

    def test_valid_candidate_accepted(self):
        reasons = _validate(VALID, set())
        self.assertEqual(reasons, [], f"Expected no rejection, got: {reasons}")

    def test_hallucinated_number_rejected(self):
        reasons = _validate(HALLUCINATED, set())
        self.assertTrue(
            any("hallucinated_number" in r for r in reasons),
            f"Expected hallucinated_number reason, got: {reasons}",
        )
        # 99 is the hallucinated number
        self.assertTrue(any("99" in r for r in reasons))

    def test_unknown_op_rejected(self):
        reasons = _validate(UNKNOWN_OP, set())
        self.assertTrue(
            any("unknown_op" in r for r in reasons),
            f"Expected unknown_op reason, got: {reasons}",
        )

    def test_duplicate_rejected(self):
        existing = self._existing()
        reasons = _validate(DUPLICATE, existing)
        self.assertTrue(
            any("duplicate" in r for r in reasons),
            f"Expected duplicate reason, got: {reasons}",
        )

    def test_prompt_too_long_rejected(self):
        cand = dict(VALID)
        cand["prompt"] = "A" * 181 + "?"
        reasons = _validate(cand, set())
        self.assertTrue(any("prompt_too_long" in r for r in reasons))

    def test_prompt_no_question_mark_rejected(self):
        cand = dict(VALID)
        cand["prompt"] = "You spent $120 at Chipotle."
        reasons = _validate(cand, set())
        self.assertTrue(any("prompt_no_question_mark" in r for r in reasons))

    def test_unknown_operation_rejected(self):
        cand = dict(VALID)
        cand["operation"] = "invented_operation"
        reasons = _validate(cand, set())
        self.assertTrue(any("unknown_operation" in r for r in reasons))

    def test_compute_error_rejected(self):
        cand = {
            "prompt": "What?",
            "skill_id": "money_arithmetic",
            "operation": "charge_total",
            "op": "div",
            "args": [10, 0],   # division by zero
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(any("compute_error" in r for r in reasons))

    def test_negative_number_accepted(self):
        """Prompt with negative number -15 in args should be accepted."""
        cand = {
            "prompt": "How much colder is -15 than 5?",
            "skill_id": "weather_math",
            "operation": "temp_delta",
            "op": "delta",
            "args": [-15, 5],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertEqual(reasons, [], f"Expected no rejection for negative number, got: {reasons}")

    def test_negative_number_hallucinated_rejected(self):
        """Prompt with negative number -15 not in args should be rejected."""
        cand = {
            "prompt": "You spent $-15 in credits. What is 10 percent?",
            "skill_id": "money_arithmetic",
            "operation": "restaurant_tip_15",
            "op": "pct_of",
            "args": [10, 15],  # 15 without minus sign
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("hallucinated_number" in r for r in reasons),
            f"Expected hallucinated_number reason for -15, got: {reasons}",
        )
        self.assertTrue(any("-15" in r for r in reasons))

    def test_comma_formatted_number_hallucinated_rejected(self):
        """Prompt with $1,250 not in args should be rejected."""
        cand = {
            "prompt": "You spent $1,250 at X. What is half?",
            "skill_id": "money_arithmetic",
            "operation": "charge_total",
            "op": "div",
            "args": [625, 2],  # 1250 missing (625 is half, but 1250 not in args)
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("hallucinated_number" in r for r in reasons),
            f"Expected hallucinated_number reason for 1250, got: {reasons}",
        )
        self.assertTrue(any("1250" in r for r in reasons))

    def test_comma_formatted_number_accepted(self):
        """Prompt with $1,250 in args should be accepted."""
        cand = {
            "prompt": "You spent $1,250 at X. What is half?",
            "skill_id": "money_arithmetic",
            "operation": "charge_total",
            "op": "div",
            "args": [1250, 2],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertEqual(reasons, [], f"Expected no rejection for comma-formatted number, got: {reasons}")


class TestRun(unittest.TestCase):
    """Integration test for run() — _call_llm and _build_context are mocked."""

    def _pool_with_duplicate(self, tmp_path: Path) -> Path:
        pool_file = tmp_path / "pool.json"
        pool_file.write_text(json.dumps([EXISTING_POOL_ENTRY]))
        return pool_file

    def test_exactly_one_accepted(self, tmp_path=None):
        """With the four canned candidates and the pre-existing duplicate,
        exactly one (VALID) should be accepted."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pool_path = self._pool_with_duplicate(Path(td))

            with patch("tools.template_forge._call_llm", return_value=CANNED) as mock_llm, \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                accepted, rejected = run(n=4, pool_path=pool_path)

            # LLM was called exactly once
            self.assertEqual(mock_llm.call_count, 1)

        self.assertEqual(len(accepted), 1, f"Expected 1 accepted, got {len(accepted)}: {accepted}")
        self.assertEqual(len(rejected), 3, f"Expected 3 rejected, got {len(rejected)}: {rejected}")
        self.assertEqual(accepted[0]["prompt"], VALID["prompt"])

    def test_rejection_reasons_correct(self):
        """Verify each rejected candidate has the right reason type."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pool_path = self._pool_with_duplicate(Path(td))

            with patch("tools.template_forge._call_llm", return_value=CANNED), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                accepted, rejected = run(n=4, pool_path=pool_path)

        # HALLUCINATED → hallucinated_number reason
        hallucinated_reasons = rejected.get(HALLUCINATED["prompt"], [])
        self.assertTrue(
            any("hallucinated_number" in r for r in hallucinated_reasons),
            f"hallucinated candidate reasons: {hallucinated_reasons}",
        )

        # UNKNOWN_OP → unknown_op reason
        unknown_reasons = rejected.get(UNKNOWN_OP["prompt"], [])
        self.assertTrue(
            any("unknown_op" in r for r in unknown_reasons),
            f"unknown_op candidate reasons: {unknown_reasons}",
        )

        # DUPLICATE → duplicate_prompt reason
        dup_reasons = rejected.get(DUPLICATE["prompt"], [])
        self.assertTrue(
            any("duplicate" in r for r in dup_reasons),
            f"duplicate candidate reasons: {dup_reasons}",
        )

    def test_accepted_entry_has_correct_shape(self):
        """Pool entry shape is exactly as specified."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"

            with patch("tools.template_forge._call_llm", return_value=[VALID]), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                accepted, _ = run(n=1, pool_path=pool_path)

        entry = accepted[0]
        required_keys = {"id", "prompt", "skill_id", "operation", "op", "args",
                         "source", "created_at", "used"}
        self.assertEqual(set(entry.keys()), required_keys)
        self.assertIsInstance(entry["id"], str)
        self.assertEqual(len(entry["id"]), 12)
        self.assertIsInstance(entry["created_at"], float)
        self.assertIs(entry["used"], False)
        self.assertEqual(entry["skill_id"], "money_arithmetic")

    def test_id_is_deterministic(self):
        """ID is SHA1(casefold(prompt))[:12] — same input → same id."""
        id1 = _candidate_id("Hello World?")
        id2 = _candidate_id("hello world?")
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 12)


class TestMissingApiKey(unittest.TestCase):
    """main() must exit 1 cleanly when OPENAI_API_KEY is absent."""

    def test_missing_key_exits_1(self):
        env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True), \
             patch("sys.argv", ["template_forge.py"]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 1)

    def test_missing_key_makes_no_network_call(self):
        """_call_llm must never be called when the key is missing."""
        env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True), \
             patch("sys.argv", ["template_forge.py"]), \
             patch("tools.template_forge._call_llm") as mock_llm:
            try:
                main()
            except SystemExit:
                pass
            mock_llm.assert_not_called()


class TestDryRun(unittest.TestCase):
    """--dry-run must not write to the pool file."""

    def test_dry_run_writes_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"

            with patch("tools.template_forge.POOL_PATH", pool_path), \
                 patch("tools.template_forge._call_llm", return_value=[VALID]), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT), \
                 patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}), \
                 patch("sys.argv", ["template_forge.py", "--dry-run", "--n", "1"]):
                main()

            self.assertFalse(pool_path.exists(), "dry-run must not write pool file")


if __name__ == "__main__":
    unittest.main()
