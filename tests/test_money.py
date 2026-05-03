import tempfile
import unittest
from pathlib import Path

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

    def test_generates_real_charge_total_prompt(self):
        rows = [
            {"date": "2026-01-02", "payee": "Trader Joe's", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "Trader Joe's", "category": "Groceries", "amount": 33.01},
        ]

        problem = money.generate_problem(rows, target={"operation": "charge_total"})

        self.assertIn("Trader Joe's", problem["prompt"])
        self.assertIn("$18.42", problem["prompt"])
        self.assertIn("$33.01", problem["prompt"])
        self.assertEqual(problem["expected"], 51.43)
        self.assertEqual(problem["parameters"]["operation"], "charge_total")

    def test_generates_category_difference_prompt(self):
        rows = [
            {"date": "2026-01-02", "payee": "A", "category": "Groceries", "amount": 18.42},
            {"date": "2026-01-08", "payee": "B", "category": "Groceries", "amount": 33.01},
            {"date": "2026-01-10", "payee": "C", "category": "Travel:Rental Car & Taxi", "amount": 12.50},
            {"date": "2026-01-12", "payee": "D", "category": "Travel:Rental Car & Taxi", "amount": 16.25},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_difference"})

        self.assertIn("January 2026", problem["prompt"])
        self.assertIn("Groceries", problem["prompt"])
        self.assertIn("Travel:Rental Car & Taxi", problem["prompt"])
        self.assertEqual(problem["expected"], 22.68)
        self.assertEqual(problem["parameters"]["operation"], "category_difference")

    def test_generates_category_share_prompt(self):
        rows = [
            {"date": "2026-01-02", "payee": "A", "category": "Groceries", "amount": 25.00},
            {"date": "2026-01-10", "payee": "B", "category": "Dining & Drinks:Restaurants", "amount": 75.00},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_share"})

        self.assertIn("January 2026", problem["prompt"])
        self.assertIn("Dining & Drinks:Restaurants", problem["prompt"])
        self.assertIn("$100.00", problem["prompt"])
        self.assertEqual(problem["expected"], 75.0)
        self.assertEqual(problem["parameters"]["operation"], "category_share")

    def test_category_prompts_avoid_large_fixed_categories(self):
        rows = [
            {"date": "2026-01-01", "payee": "Mortgage", "category": "Home:Mortgage", "amount": 5350.00},
            {"date": "2026-01-02", "payee": "A", "category": "Groceries", "amount": 25.00},
            {"date": "2026-01-10", "payee": "B", "category": "Dining & Drinks:Restaurants", "amount": 75.00},
        ]

        problem = money.generate_problem(rows, target={"operation": "category_share"})

        self.assertNotIn("Home:Mortgage", problem["prompt"])

    def test_money_attempts_key_by_operation(self):
        key = diagnosis.fact_key("money_arithmetic", {"operation": "category_difference"})

        self.assertEqual(key, "money:category_difference")
        self.assertEqual(diagnosis.fact_display(key), "Money: category difference")

    def test_generator_supports_money_arithmetic(self):
        problem = generator.generate(
            "money_arithmetic",
            target={"operation": "category_difference"},
        )

        self.assertEqual(problem["parameters"]["operation"], "category_difference")
        self.assertIsInstance(problem["expected"], float)

    def test_scheduler_converts_money_fact_key_to_target_operation(self):
        target = scheduler._fact_key_to_target("money:category_share")

        self.assertEqual(target, {"operation": "category_share"})


if __name__ == "__main__":
    unittest.main()
