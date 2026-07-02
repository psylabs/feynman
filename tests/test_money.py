import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import diagnosis, generator, money, scheduler


CSV = """Date,Account,Payee,Category,Exclusion,Amount
"Jan 2, 2026",Card,Trader Joe's,Groceries,no, -18.42
"Jan 8, 2026",Card,Trader Joe's,Groceries,no, -33.01
"Jan 9, 2026",Card,Payroll,Personal Income:Paycheck,yes, 1000.00
"Jan 10, 2026",Card,Lyft,Travel:Rental Car & Taxi,no, -12.50
"Jan 12, 2026",Card,Lyft,Travel:Rental Car & Taxi,no, -16.25
"Jan 13, 2026",Card,Amazon,Shopping,no, -40.00
"""


class MoneyTests(unittest.TestCase):
    def test_load_transactions_keeps_only_included_expenses(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "transactions.csv"
            path.write_text(CSV)

            rows = money.load_transactions(path)

        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["amount"] > 0 for r in rows))
        self.assertTrue(all(r["excluded"] is False for r in rows))

    def test_load_recent_plaid_filters_window_and_labels_days(self):
        import json
        payload = {
            "until": "2026-06-20",
            "transactions": [
                {"date": "2026-06-19", "merchant": "DoNotDisturb", "amount": 723.0,
                 "category": "FOOD_AND_DRINK", "pending": False},
                {"date": "2026-06-01", "merchant": "OldDinner", "amount": 200.0,
                 "category": "FOOD_AND_DRINK", "pending": False},  # outside window
                {"date": "2026-06-18", "merchant": "Refund", "amount": -50.0,
                 "category": "FOOD_AND_DRINK", "pending": False},  # credit, skipped
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "latest.json"
            path.write_text(json.dumps(payload))
            rows = money.load_recent_plaid_transactions(path, window_days=7)

        self.assertEqual([r["payee"] for r in rows], ["DoNotDisturb"])
        self.assertEqual(rows[0]["when"], "yesterday")

    def test_split_bill_respects_max_party_and_divides_evenly(self):
        rows = [{"date": "2026-01-02", "payee": "Diner", "category": "Dining", "amount": 84.0}]
        for _ in range(50):
            base = money.generate_problem(rows, target={"operation": "split_bill", "max_party": 5})
            self.assertIn(base["parameters"]["people"], (3, 4, 5))
            adv = money.generate_problem(rows, target={"operation": "split_bill", "max_party": 8})
            self.assertIn(adv["parameters"]["people"], (3, 4, 5, 6, 7, 8))
            self.assertEqual(
                base["parameters"]["bill_amount"],
                base["expected"] * base["parameters"]["people"],
            )

    def test_pick_finance_charges_returns_distinct_merchants(self):
        rows = [
            {"date": "2026-06-19", "payee": m, "category": "Dining", "amount": amt}
            for m, amt in [("Vivibowl", 36.0), ("Sweetgreen", 17.0), ("Chopt", 16.0)]
        ]
        for _ in range(50):
            picks = money.pick_finance_charges(rows)
            self.assertIsNotNone(picks["restaurant_tip_15"])
            self.assertIsNotNone(picks["split_bill"])
            self.assertNotEqual(
                picks["restaurant_tip_15"]["payee"], picks["split_bill"]["payee"]
            )

    def test_restaurant_tip_15_is_fifteen_percent_rounded(self):
        rows = [{"date": "2026-01-02", "payee": "Diner", "category": "Food & Dining", "amount": 80.0}]
        p = money.generate_problem(rows, target={"operation": "restaurant_tip_15"})
        self.assertEqual(p["parameters"]["tip_percent"], 15)
        self.assertEqual(p["expected"], round(p["parameters"]["bill_amount"] * 0.15))

    def test_generates_real_charge_total_prompt(self):
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]

        problem = money.generate_problem(rows, target={"operation": "charge_total"})

        # Numbers are pre-rounded ("swagged") in the prompt; expected is the
        # sum of the swagged numbers, not of the raw cents.
        self.assertIn("Trader Joe's", problem["prompt"])
        self.assertIn("$18", problem["prompt"])
        self.assertIn("$33", problem["prompt"])
        self.assertNotIn("$18.42", problem["prompt"])
        self.assertEqual(problem["expected"], 51.0)
        self.assertEqual(problem["parameters"]["operation"], "charge_total")

    def test_generates_category_difference_prompt(self):
        rows = [
            {"date": "2026-01-02", "payee": "A", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "B", "category": "Groceries", "amount": 33.01},
            {"date": "2026-01-10", "payee": "C", "category": "Travel:Rental Car & Taxi", "amount": 12.50},
            {"date": "2026-01-12", "payee": "D", "category": "Travel:Rental Car & Taxi", "amount": 16.25},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_difference"})

        # Category leaf in the prompt — no parent prefix that's awkward to TTS.
        self.assertIn("January 2026", problem["prompt"])
        self.assertIn("Groceries", problem["prompt"])
        self.assertIn("Rental Car & Taxi", problem["prompt"])
        self.assertNotIn("Travel:Rental Car & Taxi", problem["prompt"])
        # 51.43 swag → 51; 28.75 swag → 29; diff = 22
        self.assertEqual(problem["expected"], 22.0)
        self.assertEqual(problem["parameters"]["operation"], "category_difference")

    def test_category_amount_prompt_shape_and_expected(self):
        # Groceries = $250 (25% of $1000 total); Dining = $750 (75%).
        # 25% snaps to 25 (0pp error) → ACCEPT; 75% → nearest 50 (25pp) → REJECT.
        rows = [
            {"date": "2026-01-10", "payee": "A", "category": "Groceries", "amount": 250.00},
            {"date": "2026-01-15", "payee": "B", "category": "Dining", "amount": 750.00},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_amount"})

        self.assertIn("January 2026", problem["prompt"])
        self.assertIn("25%", problem["prompt"])
        self.assertIn("$1,000", problem["prompt"])
        self.assertIn("Groceries", problem["prompt"])
        self.assertIn("About how much is that?", problem["prompt"])
        # expected = swag(1000) * 25 / 100 = 1000 * 25 / 100 = 250
        self.assertEqual(problem["expected"], 250.0)
        self.assertEqual(problem["parameters"]["operation"], "category_amount")

    def test_category_amount_falls_back_when_no_friendly_snap(self):
        # Groceries = 35% (nearest friendly: 30 or 40, both 5pp away → REJECT).
        # Dining = 65% (nearest friendly: 50, 15pp away → REJECT).
        # After 10 tries with only these two categories, falls back to charge_total.
        rows = [
            {"date": "2026-01-10", "payee": "A", "category": "Groceries", "amount": 35.00},
            {"date": "2026-01-15", "payee": "B", "category": "Dining", "amount": 65.00},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_amount"})

        self.assertNotEqual(problem["parameters"]["operation"], "category_amount")

    def test_category_amount_parameters(self):
        # Two equal categories → 50% each → snaps to 50 (0pp error).
        rows = [
            {"date": "2026-01-10", "payee": "A", "category": "Groceries", "amount": 500.00},
            {"date": "2026-01-15", "payee": "B", "category": "Dining", "amount": 500.00},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_amount"})

        params = problem["parameters"]
        self.assertEqual(params["operation"], "category_amount")
        self.assertEqual(params["percentage"], 50)
        self.assertEqual(params["total"], 1000)
        self.assertIn("category", params)
        self.assertIn("month", params)

    def test_money_attempts_key_by_operation(self):
        key = diagnosis.fact_key("money_arithmetic", {"operation": "category_difference"})

        self.assertEqual(key, "money:category_difference")
        self.assertEqual(diagnosis.fact_display(key), "Money: category difference")

    def test_generator_supports_money_arithmetic(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "transactions.csv"
            path.write_text(CSV)
            rows = money.load_transactions(path)
            with patch("server.money.load_transactions", return_value=rows):
                problem = generator.generate(
                    "money_arithmetic",
                    target={"operation": "category_difference"},
                )

        self.assertEqual(problem["parameters"]["operation"], "category_difference")
        self.assertIsInstance(problem["expected"], float)

    def test_scheduler_converts_money_fact_key_to_target_operation(self):
        target = scheduler._fact_key_to_target("money:category_amount")

        self.assertEqual(target, {"operation": "category_amount"})


if __name__ == "__main__":
    unittest.main()
