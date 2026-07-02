import time
import unittest
from unittest.mock import patch

from server import scheduler


def _mul_compound_attempts(n=6, a=13, b=4, correct=0, latency=6000, start=0):
    """Attempts classifying to compound family 'mul.2dx1d' (not a SKINS prefix,
    not a bootstrap family). Wrong + slow => a weak family."""
    return [
        {
            "skill_id": "multiplication",
            "parameters": {"a": a, "b": b, "level": 2},
            "correct": correct,
            "skipped": 0,
            "resolution_latency_ms": latency,
            "onset_latency_ms": 1000,
            "created_at": start + i,
        }
        for i in range(n)
    ]


def _sub_compound_attempts(n=6, a=47, b=28, correct=0, latency=6000, start=100):
    """Attempts classifying to a 'sub.2d-2d.*' family (matches SKINS 'sub.2d')."""
    return [
        {
            "skill_id": "subtraction",
            "parameters": {"a": a, "b": b, "level": 2},
            "correct": correct,
            "skipped": 0,
            "resolution_latency_ms": latency,
            "onset_latency_ms": 1000,
            "created_at": start + i,
        }
        for i in range(n)
    ]


def _pct_attempts(n=6, pct=15, correct=0, latency=6000, start=200):
    """Attempts classifying to family 'pct' (matches SKINS 'pct')."""
    return [
        {
            "skill_id": "percent_of",
            "parameters": {"percentage": pct, "base": 80, "level": 2},
            "correct": correct,
            "skipped": 0,
            "resolution_latency_ms": latency,
            "onset_latency_ms": 1000,
            "created_at": start + i,
        }
        for i in range(n)
    ]


class FakeStorage:
    def __init__(self, skill_ids=None, attempts=None, due=None, counts=None,
                 cooldowns=None, excluded=None):
        self.skill_ids = skill_ids or [
            "multiplication", "division", "addition", "subtraction",
            "money_arithmetic", "weather_math", "percent_of",
        ]
        self.attempts = attempts or []
        self.due = due or []
        self.counts = counts or {}
        self._cooldowns = cooldowns
        self._excluded = excluded

    def all_attempts_for_user(self, user_id, limit=500):
        return self.attempts[:limit]

    def all_skill_ids(self):
        return self.skill_ids

    def get_skill(self, skill_id):
        return {"id": skill_id, "target_latency_ms": 1800}

    def skill_attempt_count(self, user_id, skill_id):
        return self.counts.get(skill_id, 0)

    def due_items(self, user_id, now, limit=50):
        return self.due[:limit]


class FakeStorageWithExclusions(FakeStorage):
    def active_cooldowns(self, user_id, now):
        return set(self._cooldowns or set())

    def excluded_keys(self, user_id):
        return set(self._excluded or set())


def _noop(*a, **k):
    pass


class SlotShapeTests(unittest.TestCase):
    REQUIRED_KEYS = {
        "skill_id", "target_fact", "level", "fact_key", "display", "role",
        "reason", "target_ms", "diagnosis_median_latency_ms",
        "diagnosis_accuracy", "diagnosis_n", "diagnosis_gap_ratio",
        "family", "covers",
    }

    def test_every_slot_has_the_expected_shape(self):
        storage = FakeStorage(attempts=_mul_compound_attempts())
        plan = scheduler.build_session_plan(storage, "u", 6, _noop)
        self.assertTrue(plan)
        for s in plan:
            self.assertTrue(self.REQUIRED_KEYS.issubset(s.keys()), s.keys())
            # orchestrator reads these directly
            self.assertIn(s["skill_id"], storage.skill_ids)
            self.assertIsInstance(s["level"], int)


class TargetTranslatorTests(unittest.TestCase):
    def test_multiplication_primitive(self):
        self.assertEqual(scheduler._target_from_item_key("mul:6x7"), {"a": 6, "b": 7})

    def test_addition_primitive(self):
        self.assertEqual(scheduler._target_from_item_key("add:5+8"), {"a": 5, "b": 8})

    def test_subtraction_primitive(self):
        self.assertEqual(scheduler._target_from_item_key("sub:18-4"), {"a": 18, "b": 4})

    def test_division_primitive_divisor_quotient(self):
        # div:7x8 -> 56 / 7 = 8
        t = scheduler._target_from_item_key("div:7x8")
        self.assertEqual(t, {"a": 56, "b": 7})
        self.assertEqual(t["a"] / t["b"], 8)

    def test_percent_key(self):
        self.assertEqual(scheduler._target_from_item_key("pct:15"), {"percentage": 15})

    def test_money_key(self):
        self.assertEqual(
            scheduler._target_from_item_key("money:split_bill"),
            {"operation": "split_bill"},
        )

    def test_compound_family_key_has_no_target(self):
        self.assertIsNone(scheduler._target_from_item_key("sub.3d-2d.bt"))
        self.assertIsNone(scheduler._target_from_item_key("div.multi"))


class DueFirstTests(unittest.TestCase):
    def test_due_items_come_before_weak_family_drills(self):
        now = time.time()
        due = [{"item_key": "mul:6x7", "tier": "primitive",
                "family": "mul.x7", "due_at": now - 10 * 86400}]
        storage = FakeStorage(attempts=_mul_compound_attempts(), due=due)
        # Isolate selection order from the cosmetic spread/alternation reshuffle.
        with patch("server.scheduler._spread_duplicates_with_alternation",
                   side_effect=lambda s: s):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
        roles = [s["role"] for s in plan]
        due_idx = next(i for i, r in enumerate(roles) if r.startswith("due"))
        weak_idx = next(i for i, r in enumerate(roles) if r.startswith("weak"))
        self.assertLess(due_idx, weak_idx)
        self.assertEqual(plan[due_idx]["fact_key"], "mul:6x7")

    def test_due_items_fill_capacity_before_weak(self):
        now = time.time()
        due = [
            {"item_key": f"mul:{i}x7", "tier": "primitive",
             "family": "mul.x7", "due_at": now - (10 + i) * 86400}
            for i in range(2, 6)
        ]
        storage = FakeStorage(attempts=_mul_compound_attempts(), due=due)
        plan = scheduler.build_session_plan(storage, "u", 2, _noop)
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(s["role"].startswith("due") for s in plan))


class BootstrapTests(unittest.TestCase):
    def test_zero_division_user_gets_bootstrap_slot(self):
        # A user with lots of multiplication but no division: bootstrap must
        # inject at least one mul:/div: primitive.
        storage = FakeStorage(attempts=_mul_compound_attempts(n=6))
        plan = scheduler.build_session_plan(storage, "u", 8, _noop)
        boot = [s for s in plan if s["role"].startswith(scheduler.BOOTSTRAP_ROLE)]
        self.assertGreaterEqual(len(boot), 1)
        self.assertTrue(
            any(s["fact_key"].startswith(("div:", "mul:")) for s in boot),
            [s["fact_key"] for s in boot],
        )


class GroundedShareTests(unittest.TestCase):
    def test_seven_slot_session_gets_two_to_four_grounded(self):
        attempts = (
            _sub_compound_attempts()
            + _pct_attempts()
            + _mul_compound_attempts(a=14, b=3)  # another weak family
        )
        storage = FakeStorage(attempts=attempts)
        plan = scheduler.build_session_plan(storage, "u", 7, _noop)
        self.assertEqual(len(plan), 7)
        grounded = [s for s in plan
                    if s["skill_id"] in ("money_arithmetic", "weather_math")]
        self.assertGreaterEqual(len(grounded), 2)
        self.assertLessEqual(len(grounded), 4)
        # skinned slots keep a `covers` pointer back to the skeleton item
        for s in grounded:
            if s["role"].endswith("+skin"):
                self.assertIsNotNone(s["covers"])

    def test_tip_split_not_hardcoded_into_every_plan(self):
        attempts = _sub_compound_attempts() + _pct_attempts()
        storage = FakeStorage(attempts=attempts)
        have_tip = 0
        for _ in range(10):
            plan = scheduler.build_session_plan(storage, "u", 8, _noop)
            ops = [
                (s.get("target_fact") or {}).get("operation")
                for s in plan
                if s["skill_id"] == "money_arithmetic"
            ]
            if "restaurant_tip_15" in ops:
                have_tip += 1
        self.assertLess(have_tip, 10)


class RecentKeyExclusionTests(unittest.TestCase):
    def _due_and_recent(self, due_offset_days):
        now = time.time()
        # recent attempt classifies to mul:6x7
        recent = [{
            "skill_id": "multiplication",
            "parameters": {"a": 6, "b": 7, "level": 1},
            "correct": 1, "skipped": 0,
            "resolution_latency_ms": 900, "onset_latency_ms": 700,
            "created_at": now,
        }]
        attempts = recent + _mul_compound_attempts()
        due = [{"item_key": "mul:6x7", "tier": "primitive",
                "family": "mul.x7", "due_at": now - due_offset_days * 86400}]
        storage = FakeStorage(attempts=attempts, due=due)
        return scheduler.build_session_plan(storage, "u", 6, _noop)

    def test_recent_non_overdue_key_is_excluded(self):
        plan = self._due_and_recent(due_offset_days=0.01)  # ~15 min overdue
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertNotIn("mul:6x7", due_keys)

    def test_recent_key_included_when_more_than_a_day_overdue(self):
        plan = self._due_and_recent(due_offset_days=2)
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertIn("mul:6x7", due_keys)


class ExclusionStubTests(unittest.TestCase):
    def test_missing_cooldown_and_excluded_methods_default_to_empty(self):
        # FakeStorage does NOT define active_cooldowns/excluded_keys — must not raise.
        storage = FakeStorage(attempts=_mul_compound_attempts())
        plan = scheduler.build_session_plan(storage, "u", 5, _noop)
        self.assertTrue(plan)

    def test_excluded_keys_are_skipped(self):
        now = time.time()
        due = [{"item_key": "mul:6x7", "tier": "primitive",
                "family": "mul.x7", "due_at": now - 5 * 86400}]
        storage = FakeStorageWithExclusions(
            attempts=_mul_compound_attempts(), due=due,
            excluded={"mul:6x7"},
        )
        plan = scheduler.build_session_plan(storage, "u", 6, _noop)
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertNotIn("mul:6x7", due_keys)


if __name__ == "__main__":
    unittest.main()
