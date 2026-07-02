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
#    operation, prompt ≤100 chars ends with ?, not duplicate.
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
        """Prompts over 100 chars (new cap, was 180) must be rejected."""
        cand = dict(VALID)
        cand["prompt"] = "A" * 100 + "?"  # 101 chars > 100
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
        """Prompt with $1,250 in args should be accepted (comma normalization)."""
        cand = {
            "prompt": "You spent $1,250 at X. Split 2 ways — each share?",
            "skill_id": "money_arithmetic",
            "operation": "charge_total",
            "op": "div",
            "args": [1250, 2],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertEqual(reasons, [], f"Expected no rejection for comma-formatted number, got: {reasons}")

    # -----------------------------------------------------------------------
    # New tests: one per new rejection reason (§2 of task-6.1 spec)
    # -----------------------------------------------------------------------

    def test_numberless_prompt_rejected(self):
        """Prompt with zero numbers fails too_few_numbers (and args_not_in_prompt).
        Models the real failure: 'What is the total amount spent on transportation yesterday?'"""
        cand = {
            "prompt": "What is the total amount spent on transportation yesterday?",
            "skill_id": "money_arithmetic",
            "operation": "category_difference",
            "op": "delta",
            "args": [36, 22],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("too_few_numbers" in r for r in reasons),
            f"Expected too_few_numbers, got: {reasons}",
        )
        # All args must also be flagged as absent from the prompt
        self.assertTrue(
            any("args_not_in_prompt" in r for r in reasons),
            f"Expected args_not_in_prompt, got: {reasons}",
        )

    def test_div_not_integer_rejected(self):
        """Division that yields a non-integer result must be rejected.
        Models the real failure: $36 / $14 ≈ 2.57."""
        cand = {
            "prompt": "Amazon $36, entertainment $14. What's the ratio?",
            "skill_id": "money_arithmetic",
            "operation": "category_difference",
            "op": "div",
            "args": [36, 14],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("div_not_integer" in r for r in reasons),
            f"Expected div_not_integer, got: {reasons}",
        )

    def test_sub_negative_rejected(self):
        """Subtraction producing a negative result must be rejected.
        Models the real failure: $14 − $22 = −$8."""
        cand = {
            "prompt": "Entertainment $14, Amazon $22. What's the difference?",
            "skill_id": "money_arithmetic",
            "operation": "category_difference",
            "op": "sub",
            "args": [14, 22],
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("sub_negative" in r for r in reasons),
            f"Expected sub_negative, got: {reasons}",
        )

    def test_domain_mix_dollar_in_weather_rejected(self):
        """weather_math prompt containing '$' must be rejected.
        Models the real failure: money amounts injected into a weather question."""
        cand = {
            "prompt": "High $89, low $72. What's the range?",
            "skill_id": "weather_math",
            "operation": "daily_range",
            "op": "delta",
            "args": [89, 72],
            "source": "open-meteo",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("domain_mix" in r for r in reasons),
            f"Expected domain_mix, got: {reasons}",
        )

    def test_args_not_in_prompt_rejected(self):
        """An arg that does not appear as a number in the prompt must be rejected."""
        cand = {
            "prompt": "You spent $120 at Chipotle. What is 15 percent of that?",
            "skill_id": "money_arithmetic",
            "operation": "restaurant_tip_15",
            "op": "pct_of",
            "args": [15, 999],   # 999 is not in the prompt
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("args_not_in_prompt" in r for r in reasons),
            f"Expected args_not_in_prompt, got: {reasons}",
        )
        self.assertTrue(any("999" in r for r in reasons))

    def test_args_not_in_context_rejected(self):
        """Args that are not a subset of the context numbers for their skill must be rejected."""
        cand = {
            "prompt": "Amazon $36, PRO $22. What's the difference?",
            "skill_id": "money_arithmetic",
            "operation": "category_difference",
            "op": "delta",
            "args": [36, 22],
            "source": "test",
        }
        # Context only contains {50, 75} — neither 36 nor 22 appears in it
        context_numbers = {"money_arithmetic": {50.0, 75.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        self.assertTrue(
            any("args_not_in_context" in r for r in reasons),
            f"Expected args_not_in_context, got: {reasons}",
        )

    def test_args_in_context_accepted(self):
        """When args are in context, context grounding check passes."""
        cand = {
            "prompt": "Amazon $36, PRO $22. What's the difference?",
            "skill_id": "money_arithmetic",
            "operation": "category_difference",
            "op": "delta",
            "args": [36, 22],
            "source": "test",
        }
        context_numbers = {"money_arithmetic": {36.0, 22.0, 50.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        self.assertFalse(
            any("args_not_in_context" in r for r in reasons),
            f"Did not expect args_not_in_context, got: {reasons}",
        )

    def test_context_check_skipped_when_none(self):
        """When context_numbers is None (default), context grounding is skipped."""
        cand = dict(VALID)
        # No context_numbers passed → check skipped → VALID accepted
        reasons = _validate(cand, set(), context_numbers=None)
        self.assertEqual(reasons, [], f"Expected no rejection without context, got: {reasons}")

    def test_pct_unfriendly_rejected(self):
        """pct_of with a non-friendly percent must be rejected."""
        cand = {
            "prompt": "Open Source $15, total $36. About how much is 41 percent?",
            "skill_id": "money_arithmetic",
            "operation": "category_amount",
            "op": "pct_of",
            "args": [41, 36],   # 41 is not in FRIENDLY_PCTS
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("pct_unfriendly" in r for r in reasons),
            f"Expected pct_unfriendly, got: {reasons}",
        )

    def test_too_many_addends_rejected(self):
        """add_list with more than 4 addends must be rejected."""
        cand = {
            "prompt": "Charges: $10, $20, $30, $40, $50. What's the total?",
            "skill_id": "money_arithmetic",
            "operation": "charge_total",
            "op": "add_list",
            "args": [10, 20, 30, 40, 50],   # 5 addends > 4 max
            "source": "test",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("too_many_addends" in r for r in reasons),
            f"Expected too_many_addends, got: {reasons}",
        )

    # -----------------------------------------------------------------------
    # pct_of / div context carve-outs (task-6.1 refine-2, reviewer Important)
    # -----------------------------------------------------------------------

    def test_pct_of_friendly_percent_exempt_base_in_context_accepted(self):
        """pct_of(15, base) is accepted when 15 is a friendly pct (chosen
        constant, not required in context) and the base IS in context."""
        cand = {
            "prompt": "You spent $120 at Chipotle on Friday. What's a 15% tip?",
            "skill_id": "money_arithmetic",
            "operation": "restaurant_tip_15",
            "op": "pct_of",
            "args": [15, 120],
            "source": "test",
        }
        context_numbers = {"money_arithmetic": {120.0, 36.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        self.assertEqual(reasons, [], f"Expected no rejection, got: {reasons}")

    def test_pct_of_friendly_percent_base_not_in_context_rejected(self):
        """The percent (15) is exempt, but the base still must be grounded —
        a base not present in context is rejected via the base arg."""
        cand = {
            "prompt": "You spent $999 at Chipotle on Friday. What's a 15% tip?",
            "skill_id": "money_arithmetic",
            "operation": "restaurant_tip_15",
            "op": "pct_of",
            "args": [15, 999],
            "source": "test",
        }
        context_numbers = {"money_arithmetic": {120.0, 36.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        context_reasons = [r for r in reasons if "args_not_in_context" in r]
        self.assertTrue(context_reasons, f"Expected args_not_in_context, got: {reasons}")
        # Only the base (999) should be flagged, not the exempt percent (15)
        self.assertIn("999", context_reasons[0])
        self.assertNotIn("15.0", context_reasons[0])

    def test_div_small_divisor_exempt_dividend_in_context_accepted(self):
        """div(amount, 4) is accepted when 4 is a small party-size divisor
        (chosen constant, not required in context) and the amount IS."""
        cand = {
            "prompt": "You spent $80 at PRO. Split 4 ways — each share?",
            "skill_id": "money_arithmetic",
            "operation": "split_bill",
            "op": "div",
            "args": [80, 4],
            "source": "test",
        }
        context_numbers = {"money_arithmetic": {80.0, 36.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        self.assertEqual(reasons, [], f"Expected no rejection, got: {reasons}")

    def test_div_small_divisor_dividend_not_in_context_rejected(self):
        """The divisor (4) is exempt, but the dividend still must be
        grounded — a dividend not present in context is rejected."""
        cand = {
            "prompt": "You spent $1,000 at PRO. Split 4 ways — each share?",
            "skill_id": "money_arithmetic",
            "operation": "split_bill",
            "op": "div",
            "args": [1000, 4],  # 1000 not in context; 4 is exempt divisor
            "source": "test",
        }
        context_numbers = {"money_arithmetic": {80.0, 36.0}, "weather_math": set()}
        reasons = _validate(cand, set(), context_numbers)
        self.assertTrue(
            any("args_not_in_context" in r for r in reasons),
            f"Expected args_not_in_context, got: {reasons}",
        )
        self.assertTrue(any("1000" in r for r in reasons))

    def test_ugly_answer_zero_rejected(self):
        """delta with equal args (result = 0) must be rejected as ugly_answer."""
        cand = {
            "prompt": "High 72, low 72. What's the range?",
            "skill_id": "weather_math",
            "operation": "daily_range",
            "op": "delta",
            "args": [72, 72],
            "source": "open-meteo",
        }
        reasons = _validate(cand, set())
        self.assertTrue(
            any("ugly_answer" in r for r in reasons),
            f"Expected ugly_answer, got: {reasons}",
        )


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

    def test_run_context_numbers_none_skips_context_check(self):
        """run() with no context_numbers injected skips args_not_in_context."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"

            with patch("tools.template_forge._call_llm", return_value=[VALID]), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                # context_numbers not passed → None → context check skipped
                accepted, rejected = run(n=1, pool_path=pool_path)

        self.assertEqual(len(accepted), 1, f"Expected 1 accepted, got: {rejected}")

    def test_op_quota_caps_at_three_per_operation(self):
        """5 valid same-operation candidates → 3 accepted, 2 rejected
        with an op_quota reason (task-6.1 refine-2, §3)."""
        import tempfile

        same_op_candidates = [
            {
                "prompt": f"You spent ${amt} at Amazon and $22 at PRO on Tuesday. "
                          f"What's the difference?",
                "skill_id": "money_arithmetic",
                "operation": "category_difference",
                "op": "delta",
                "args": [amt, 22],
                "source": "test",
            }
            for amt in (36, 40, 44, 48, 52)
        ]

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"

            with patch("tools.template_forge._call_llm", return_value=same_op_candidates), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                accepted, rejected = run(n=5, pool_path=pool_path)

        self.assertEqual(len(accepted), 3, f"Expected 3 accepted, got {len(accepted)}: {accepted}")
        self.assertEqual(len(rejected), 2, f"Expected 2 rejected, got {len(rejected)}: {rejected}")
        for reasons in rejected.values():
            self.assertTrue(
                any(r == "op_quota:category_difference" for r in reasons),
                f"Expected op_quota reason, got: {reasons}",
            )

    def test_run_context_numbers_injected_rejects_out_of_context(self):
        """run() with injected context_numbers rejects args not in context."""
        import tempfile

        # VALID has args [15, 120] for skill money_arithmetic
        # Inject context with only {50, 75} → neither arg is in context
        context_numbers = {"money_arithmetic": {50.0, 75.0}, "weather_math": set()}

        with tempfile.TemporaryDirectory() as td:
            pool_path = Path(td) / "pool.json"

            with patch("tools.template_forge._call_llm", return_value=[VALID]), \
                 patch("tools.template_forge._build_context", return_value=_FAKE_CONTEXT):
                accepted, rejected = run(
                    n=1, pool_path=pool_path, context_numbers=context_numbers
                )

        self.assertEqual(len(accepted), 0, "Expected VALID rejected due to context mismatch")
        self.assertEqual(len(rejected), 1)
        reasons = list(rejected.values())[0]
        self.assertTrue(any("args_not_in_context" in r for r in reasons))


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
                 patch("tools.template_forge._build_context_numbers", return_value=None), \
                 patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}), \
                 patch("sys.argv", ["template_forge.py", "--dry-run", "--n", "1"]):
                main()

            self.assertFalse(pool_path.exists(), "dry-run must not write pool file")


if __name__ == "__main__":
    unittest.main()
