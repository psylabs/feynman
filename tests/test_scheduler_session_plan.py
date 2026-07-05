import time
import unittest
from unittest.mock import patch

from server import family_stats, scheduler


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


def _money_attempts(op="split_bill", n=6, correct=0, latency=6000, start=300):
    """Attempts classifying to grounded family 'money.<op>' (key 'money:<op>')."""
    return [
        {
            "skill_id": "money_arithmetic",
            "parameters": {"operation": op, "level": 2},
            "correct": correct,
            "skipped": 0,
            "resolution_latency_ms": latency,
            "onset_latency_ms": 1000,
            "created_at": start + i,
        }
        for i in range(n)
    ]


def _mul_primitive_attempts(a=7, b=13, n=6, correct=0, latency=6000, start=400):
    """Attempts classifying to primitive family 'mul.x13' (key 'mul:7x13')."""
    return [
        {
            "skill_id": "multiplication",
            "parameters": {"a": a, "b": b, "level": 1},
            "correct": correct,
            "skipped": 0,
            "resolution_latency_ms": latency,
            "onset_latency_ms": latency,
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
        "family", "covers", "tier",
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
        # mul:6x13 clears every active multiplication suppression rule
        # (max_operand=13 > 12, nothing <= 5 or in {10, 11}) — a legal due
        # item, unlike a retired mul:6x7 which the zombie guard now skips.
        due = [{"item_key": "mul:6x13", "tier": "primitive",
                "family": "mul.x13", "due_at": now - 10 * 86400}]
        # _mul_compound_attempts classifies to key "mul.2dx1d" (family == key for
        # compound), so it lands in recent and gets blocked from weak selection
        # (Fix 2). _pct_attempts classifies to key "pct:15" which != family "pct",
        # so "pct" family is NOT blocked by the recent-key set — giving us a visible
        # weak slot to assert ordering against.
        storage = FakeStorage(attempts=_mul_compound_attempts() + _pct_attempts(), due=due)
        # Isolate selection order from the cosmetic spread/alternation reshuffle.
        with patch("server.scheduler._spread_duplicates_with_alternation",
                   side_effect=lambda s: s):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
        roles = [s["role"] for s in plan]
        due_idx = next(i for i, r in enumerate(roles) if r.startswith("due"))
        weak_idx = next(i for i, r in enumerate(roles) if r.startswith("weak"))
        self.assertLess(due_idx, weak_idx)
        self.assertEqual(plan[due_idx]["fact_key"], "mul:6x13")

    def test_due_items_fill_capacity_before_weak(self):
        now = time.time()
        # hi=13 partners keep every fact legal (max_operand=13 > 12); lo in
        # 6-9 keeps every operand/result above the trivial_value <= 5 floor,
        # so none of these due items are suppressed/skipped.
        due = [
            {"item_key": f"mul:{i}x13", "tier": "primitive",
             "family": "mul.x13", "due_at": now - (10 + i) * 86400}
            for i in range(6, 10)
        ]
        storage = FakeStorage(attempts=_mul_compound_attempts(), due=due)
        plan = scheduler.build_session_plan(storage, "u", 2, _noop)
        self.assertEqual(len(plan), 2)
        self.assertTrue(all(s["role"].startswith("due") for s in plan))


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_families_exclude_unreachable_x12(self):
        """mul.x12/div.x12 are unreachable on fresh generation (a fact whose
        LARGER recalled operand is 12 has max_operand <= 12 and is suppressed
        by the yaml rule), so bootstrapping them would zombie-schedule: the
        pinned first-look would always be suppressed and re-sampled, and the
        family's attempt count could never organically reach 5."""
        for prefix in ("mul.x", "div.x"):
            tables = sorted(
                int(f.removeprefix(prefix))
                for f in scheduler.BOOTSTRAP_FAMILIES
                if f.startswith(prefix)
            )
            self.assertEqual(tables, [13, 14, 15, 16, 17, 18, 19], prefix)
        self.assertIn("add.bridge10", scheduler.BOOTSTRAP_FAMILIES)
        self.assertIn("sub.borrow20", scheduler.BOOTSTRAP_FAMILIES)

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
        # 16 mul_compound attempts fill RECENT_KEY_WINDOW=15 so sub.2d-2d.bo and
        # pct fall outside recency — both become valid weak candidates (Fix 2).
        # Sub slots skin to weather ops (not tip); pct slots are 50/50 tip vs
        # category_amount, so restaurant_tip_15 cannot appear in every single run.
        attempts = (
            _mul_compound_attempts(n=16, start=200)
            + _sub_compound_attempts(start=100)
            + _pct_attempts(start=0)
        )
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
        # recent attempt classifies to mul:6x13 (hi=13 clears the raised MULT_TABLE
        # floor; 6x7 would now classify compound, see docs/2026-07-05 doctrine).
        recent = [{
            "skill_id": "multiplication",
            "parameters": {"a": 6, "b": 13, "level": 1},
            "correct": 1, "skipped": 0,
            "resolution_latency_ms": 900, "onset_latency_ms": 700,
            "created_at": now,
        }]
        attempts = recent + _mul_compound_attempts()
        due = [{"item_key": "mul:6x13", "tier": "primitive",
                "family": "mul.x13", "due_at": now - due_offset_days * 86400}]
        storage = FakeStorage(attempts=attempts, due=due)
        return scheduler.build_session_plan(storage, "u", 6, _noop)

    def test_recent_non_overdue_key_is_excluded(self):
        plan = self._due_and_recent(due_offset_days=0.01)  # ~15 min overdue
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertNotIn("mul:6x13", due_keys)

    def test_recent_key_included_when_more_than_a_day_overdue(self):
        plan = self._due_and_recent(due_offset_days=2)
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertIn("mul:6x13", due_keys)


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

    def test_pick_drill_never_pins_an_excluded_fact(self):
        # pick_drill feeds the offline seed pack; excluded facts must not leak.
        # 6x7 is drilled wrong+slow so it dominates drill_priorities; excluding
        # it must keep it out of every pinned pick across many samples.
        attempts = _mul_compound_attempts(n=8, a=6, b=7, correct=0, latency=8000)
        storage = FakeStorageWithExclusions(attempts=attempts, excluded={"mul:6x7"})
        for _ in range(200):
            pick = scheduler.pick_drill(storage, "u", _noop)
            tf = pick.get("target_fact") or {}
            if tf.get("a") is not None:
                lo, hi = sorted((int(tf["a"]), int(tf["b"])))
                self.assertNotEqual(f"mul:{lo}x{hi}", "mul:6x7")


def _make_minimal_slot(skill_id, fact_key, family, tier, role="weak"):
    """Build the minimal slot dict that apply_skins inspects."""
    return {
        "skill_id": skill_id,
        "fact_key": fact_key,
        "family": family,
        "tier": tier,
        "role": role,
        "target_fact": None,
        "level": 1,
        "display": family,
        "reason": f"{role}: {family}",
        "target_ms": 1200,
        "diagnosis_median_latency_ms": None,
        "diagnosis_accuracy": None,
        "diagnosis_n": None,
        "diagnosis_gap_ratio": None,
        "covers": None,
    }


class SkinTierGuardTests(unittest.TestCase):
    """Fix 1: apply_skins must never skin PRIMITIVE-tier slots."""

    def test_due_primitive_slot_not_skinned(self):
        """A due primitive div:8x13 slot must not be skinned even when the
        grounded count is below target and 'div' matches a SKINS prefix."""
        now = time.time()
        # div:8x13 clears every active division suppression rule (max_operand,
        # from the (divisor, quotient) pair, is 13 > 12; nothing <= 5 or in
        # {10, 11}) — a legal due item, unlike a retired div:7x8 which the
        # zombie guard now skips (making this fixture's own assertions moot).
        due = [{"item_key": "div:8x13", "tier": "primitive",
                "family": "div.x13", "due_at": now - 5 * 86400}]
        # compound weak families give apply_skins something to work with
        attempts = _sub_compound_attempts() + _pct_attempts()
        storage = FakeStorage(attempts=attempts, due=due)
        # Run several times — skinning has randomness in which slot is chosen
        for _ in range(20):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
            for s in plan:
                if s.get("fact_key") == "div:8x13":
                    self.assertEqual(
                        s["skill_id"], "division",
                        f"Primitive div:8x13 was skinned to {s['skill_id']}",
                    )
                    self.assertFalse(
                        s["role"].endswith("+skin"),
                        "Primitive due slot should not carry +skin role",
                    )

    def test_bootstrap_slot_never_skinned(self):
        """Bootstrap slots are primitive; apply_skins must skip them even when
        their family (e.g. div.x7) matches a SKINS prefix."""
        boot = _make_minimal_slot("division", "div:2x7", "div.x7", "primitive", "bootstrap")
        # compound partner gives apply_skins a candidate so the budget is consumed
        compound = _make_minimal_slot("subtraction", "sub.2d-2d.bo", "sub.2d-2d.bo", "compound")
        slots = [boot, compound]
        scheduler.apply_skins(slots, 2)
        self.assertEqual(slots[0]["skill_id"], "division",
                         "Bootstrap primitive slot must not be skinned")
        self.assertFalse(slots[0]["role"].endswith("+skin"))

    def test_compound_weak_family_still_skinned(self):
        """A compound weak-family slot (sub.3d-2d.bt → SKINS 'sub.3d') must
        still be converted to a grounded op after the primitive guard is added."""
        slot = _make_minimal_slot("subtraction", "sub.3d-2d.bt", "sub.3d-2d.bt", "compound")
        slots = [slot]
        scheduler.apply_skins(slots, 1)
        self.assertIn(
            slots[0]["skill_id"], ("money_arithmetic", "weather_math"),
            "Compound slot sub.3d-2d.bt should be skinned to a grounded op",
        )
        self.assertTrue(slots[0]["role"].endswith("+skin"))


class WeakFamilyCooldownTests(unittest.TestCase):
    """Fix 2: weak-family picks must respect cooldown and recent-key exclusion."""

    def test_weak_family_in_cooldown_not_selected(self):
        """A family present in the cooldown set must not appear as a weak drill."""
        # _sub_compound_attempts(a=47, b=28) classifies to sub.2d-2d.bo
        blocked = "sub.2d-2d.bo"
        storage = FakeStorageWithExclusions(
            attempts=_sub_compound_attempts(),
            cooldowns={blocked},
        )
        for _ in range(10):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
            weak_keys = [s["fact_key"] for s in plan if s["role"].startswith("weak")]
            self.assertNotIn(
                blocked, weak_keys,
                f"Cooldown family {blocked!r} appeared as a weak slot",
            )

    def test_weak_family_in_recent_keys_not_selected(self):
        """A family recently practiced (in the last RECENT_KEY_WINDOW attempts)
        must not be selected as a weak drill."""
        # Create a recent attempt that classifies to sub.2d-2d.bo
        now = time.time()
        recent_attempt = {
            "skill_id": "subtraction",
            "parameters": {"a": 47, "b": 28, "level": 2},
            "correct": 0,
            "skipped": 0,
            "resolution_latency_ms": 6000,
            "onset_latency_ms": 1000,
            "created_at": now,
        }
        # Build enough history for sub.2d-2d.bo to be a weak family (n >= 5)
        # but put the recent attempt first so it appears in the RECENT_KEY_WINDOW
        attempts = [recent_attempt] + _sub_compound_attempts(n=5, start=now - 1000)
        storage = FakeStorage(attempts=attempts)
        for _ in range(10):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
            weak_keys = [s["fact_key"] for s in plan if s["role"].startswith("weak")]
            self.assertNotIn(
                "sub.2d-2d.bo", weak_keys,
                "Recently seen family sub.2d-2d.bo should not be a weak slot",
            )


class FamilyNamespaceBlockingTests(unittest.TestCase):
    """Fix 1: weak lane and skins must honor cooldown/exclusion across the
    family (money.split_bill) vs item-key (money:split_bill) namespace gap."""

    def test_cooled_down_money_key_not_selected_as_weak_family(self):
        """A cooled-down 'money:split_bill' must not appear as a weak drill even
        when 'money.split_bill' is the weakest family (rule b translation)."""
        storage = FakeStorageWithExclusions(
            attempts=_money_attempts(op="split_bill"),
            cooldowns={"money:split_bill"},
        )
        for _ in range(20):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
            keys = [s["fact_key"] for s in plan]
            self.assertNotIn(
                "money.split_bill", keys,
                "Cooled-down money:split_bill leaked into the weak lane",
            )
            # The grounded op key must also never be re-created by a skin.
            self.assertNotIn("money:split_bill", keys)

    def test_excluded_weather_skin_candidate_never_chosen(self):
        """apply_skins must never render a skin onto an excluded op key. With
        weather:temp_delta excluded, the sub.2d family falls back to the other
        weather op (daily_range)."""
        for _ in range(30):
            slot = _make_minimal_slot(
                "subtraction", "sub.2d-2d.bo", "sub.2d-2d.bo", "compound"
            )
            slots = [slot]
            scheduler.apply_skins(slots, 1, excluded={"weather:temp_delta"})
            if slots[0]["skill_id"] == "weather_math":
                self.assertEqual(
                    slots[0]["fact_key"], "weather:daily_range",
                    "Excluded weather:temp_delta was chosen as a skin",
                )

    def test_all_weather_skins_excluded_leaves_slot_unskinned(self):
        """When every candidate op for a skin prefix is excluded, the slot is
        left alone rather than force-skinned onto a blocked op."""
        slot = _make_minimal_slot(
            "subtraction", "sub.2d-2d.bo", "sub.2d-2d.bo", "compound"
        )
        slots = [slot]
        scheduler.apply_skins(
            slots, 1, excluded={"weather:temp_delta", "weather:daily_range"}
        )
        self.assertEqual(slots[0]["skill_id"], "subtraction")
        self.assertFalse(slots[0]["role"].endswith("+skin"))

    def test_primitive_fact_cooldown_does_not_block_weak_family(self):
        """Rule d: a cooldown on a single fact (mul:7x13) must NOT block the whole
        mul.x13 weak family — per-fact cooldowns are due-lane-only."""
        storage = FakeStorageWithExclusions(
            attempts=_mul_primitive_attempts(a=7, b=13),
            cooldowns={"mul:7x13"},
        )
        seen = False
        for _ in range(20):
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
            weak_keys = [s["fact_key"] for s in plan if s["role"].startswith("weak")]
            if "mul.x13" in weak_keys:
                seen = True
        self.assertTrue(
            seen, "mul.x13 weak drills were wrongly blocked by a mul:7x13 cooldown"
        )


class DueLaneSuppressedZombieTests(unittest.TestCase):
    """Final-review fix: a due item pinning a retired fact must be skipped
    (not served) so it doesn't zombie-schedule — see scheduler.py due loop."""

    def test_suppressed_due_item_skipped_nonsuppressed_due_item_kept(self):
        now = time.time()
        due = [
            # mul:6x7 is retired (max_operand=7 <= 12 clears the raised-floor
            # suppression rule) — pinning it would get suppressed+resampled by
            # generate(), grading under a different key and never draining.
            {"item_key": "mul:6x7", "tier": "primitive", "family": "mul.x7",
             "due_at": now - 10 * 86400},
            # mul:6x13 clears every active multiplication suppression rule
            # (max_operand=13 > 12, nothing <= 5 or in {10, 11}), so it's a
            # legitimate due review that must still fill a slot.
            {"item_key": "mul:6x13", "tier": "primitive", "family": "mul.x13",
             "due_at": now - 9 * 86400},
        ]
        storage = FakeStorage(attempts=_mul_compound_attempts(), due=due)
        with self.assertLogs("feynman.scheduler", level="INFO") as cm:
            plan = scheduler.build_session_plan(storage, "u", 6, _noop)
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertNotIn("mul:6x7", due_keys)
        self.assertIn("mul:6x13", due_keys)
        self.assertTrue(
            any("due.skip_suppressed" in msg and "mul:6x7" in msg
                for msg in cm.output),
            cm.output,
        )

    def test_real_due_item_behind_a_wall_of_zombies_still_gets_a_slot(self):
        """Zombies never drain, so they accrete at the head of ORDER BY
        due_at ASC. With a fetch limit of only length*2, enough zombies push
        every real due item out of the fetch window entirely — the due lane
        must fetch with enough headroom that a real item sitting behind more
        than length*2 zombies still lands a slot."""
        now = time.time()
        length = 6
        # More zombies than length*2 (13 > 12), all more overdue than the one
        # real item, so they fill the entire naive fetch window. Every
        # mul:2xN fact is suppressed (operand 2 <= 5, trivial_value).
        zombies = [
            {"item_key": f"mul:2x{7 + i}", "tier": "primitive",
             "family": f"mul.x{7 + i}", "due_at": now - (100 - i) * 86400}
            for i in range(length * 2 + 1)
        ]
        real = {"item_key": "mul:6x13", "tier": "primitive",
                "family": "mul.x13", "due_at": now - 5 * 86400}
        storage = FakeStorage(attempts=_mul_compound_attempts(),
                              due=zombies + [real])
        plan = scheduler.build_session_plan(storage, "u", length, _noop)
        due_keys = [s["fact_key"] for s in plan if s["role"].startswith("due")]
        self.assertIn("mul:6x13", due_keys)
        for z in zombies:
            self.assertNotIn(z["item_key"], due_keys)


if __name__ == "__main__":
    unittest.main()
