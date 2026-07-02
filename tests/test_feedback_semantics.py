"""Tests for feedback_semantics.apply() — TDD-first, written before implementation.

Covers:
- too_easy  → FSRS Easy rating on the attempt's taxon item
- too_hard  → store only (no item state change)
- seen_too_often → key_cooldown upserted with now+7*86400
- bad_problem → excluded after ≥2 distinct days, NOT after 1 day
- cooldown expiry → until in past → NOT in active_cooldowns
- bare thumb=-1, correct+fast  → grades Easy
- bare thumb=-1, wrong         → store only
- reason_code wins over thumb (both present, reason_code takes precedence)
- attempt_id=None              → no-op
- unclassifiable attempt       → no-op
- scheduler integration smoke  → after set_key_cooldown, build_session_plan
                                  excludes the key from weak-family slots
"""
import time
import unittest
from datetime import datetime, timezone

from fsrs import Rating

from server import feedback_semantics, taxonomy


# ── in-memory storage fixture ──────────────────────────────────────────────


class FakeStorage:
    """Minimal in-memory storage covering all methods used by feedback_semantics."""

    def __init__(self):
        self._attempts = {}          # attempt_id -> dict
        self._item_states = {}       # (user_id, item_key) -> dict
        self._cooldowns = {}         # (user_id, item_key) -> until
        self._excluded = {}          # (user_id, item_key) -> reason
        self._bad_problem_rows = []  # list[{attempt_id, created_at, user_id, reason_code}]
        self._skills = {}            # skill_id -> dict

    # ---- attempts ----
    def get_attempt(self, attempt_id):
        return self._attempts.get(attempt_id)

    # ---- item state ----
    def get_item_state(self, user_id, item_key):
        return self._item_states.get((user_id, item_key))

    def upsert_item_state(self, user_id, item_key, tier, family, card_json, due_at, rating):
        self._item_states[(user_id, item_key)] = {
            "user_id": user_id, "item_key": item_key, "tier": tier, "family": family,
            "card_json": card_json, "due_at": due_at, "last_rating": rating,
        }

    # ---- cooldowns ----
    def set_key_cooldown(self, user_id, item_key, until):
        self._cooldowns[(user_id, item_key)] = until

    def active_cooldowns(self, user_id, now):
        return {k for (u, k), until in self._cooldowns.items() if u == user_id and until > now}

    # ---- excluded keys ----
    def add_excluded_key(self, user_id, item_key, reason):
        self._excluded.setdefault((user_id, item_key), reason)

    def excluded_keys(self, user_id):
        return {k for (u, k) in self._excluded if u == user_id}

    # ---- bad_problem lookup ----
    def bad_problem_feedback_for_user(self, user_id):
        return [r for r in self._bad_problem_rows
                if r.get("user_id") == user_id and r.get("reason_code") == "bad_problem"]

    # ---- skills ----
    def get_skill(self, skill_id):
        return self._skills.get(skill_id)


def _make_primitive_attempt(
    attempt_id=1,
    skill_id="multiplication",
    a=6, b=7,
    correct=True,
    onset_latency_ms=900,
    resolution_latency_ms=1000,
    session_user="u1",
):
    """mul:6x7 primitive (FSRS key = mul:6x7, family = mul.x7, tier = primitive)."""
    return {
        "id": attempt_id,
        "skill_id": skill_id,
        "parameters": {"a": a, "b": b},
        "correct": correct,
        "onset_latency_ms": onset_latency_ms,
        "resolution_latency_ms": resolution_latency_ms,
        "user_id": session_user,
    }


def _make_compound_attempt(
    attempt_id=2,
    skill_id="subtraction",
    a=47, b=28,
    correct=True,
    onset_latency_ms=800,
    resolution_latency_ms=1600,
    session_user="u1",
):
    """47-28: classifies to sub.2d-2d.bo (compound).  key == family."""
    return {
        "id": attempt_id,
        "skill_id": skill_id,
        "parameters": {"a": a, "b": b},
        "correct": correct,
        "onset_latency_ms": onset_latency_ms,
        "resolution_latency_ms": resolution_latency_ms,
        "user_id": session_user,
    }


def _storage_with_attempt(attempt: dict) -> FakeStorage:
    s = FakeStorage()
    s._attempts[attempt["id"]] = attempt
    s._skills[attempt["skill_id"]] = {"id": attempt["skill_id"], "target_latency_ms": 1800}
    return s


# ── helpers ────────────────────────────────────────────────────────────────


def _apply(storage, *, user_id="u1", attempt_id=None, thumb=None, reason_code=None):
    feedback_semantics.apply(storage, user_id, attempt_id, thumb, reason_code)


# ── too_easy ───────────────────────────────────────────────────────────────


class TooEasyTests(unittest.TestCase):

    def test_too_easy_creates_item_state_with_easy_rating(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, reason_code="too_easy")
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertIsNotNone(state, "item_state should be created for too_easy")
        self.assertEqual(state["last_rating"], int(Rating.Easy))

    def test_too_easy_due_at_is_far_future(self):
        """FSRS Easy should schedule a long interval."""
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        now = time.time()
        _apply(storage, attempt_id=1, reason_code="too_easy")
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertGreater(state["due_at"], now + 7 * 86400,
                           "Easy rating should push due_at at least 7 days out")

    def test_too_easy_tier_and_family_match_taxon(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, reason_code="too_easy")
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertEqual(state["tier"], "primitive")
        self.assertEqual(state["family"], "mul.x7")


# ── too_hard ───────────────────────────────────────────────────────────────


class TooHardTests(unittest.TestCase):

    def test_too_hard_no_item_state_created(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, reason_code="too_hard")
        self.assertIsNone(storage.get_item_state("u1", "mul:6x7"))

    def test_too_hard_no_cooldown(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, reason_code="too_hard")
        self.assertEqual(storage.active_cooldowns("u1", time.time()), set())


# ── seen_too_often ──────────────────────────────────────────────────────────


class SeenTooOftenTests(unittest.TestCase):

    def test_seen_too_often_adds_cooldown(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        before = time.time()
        _apply(storage, attempt_id=1, reason_code="seen_too_often")
        now = time.time()
        until = storage._cooldowns.get(("u1", "mul:6x7"))
        self.assertIsNotNone(until)
        self.assertAlmostEqual(until, before + 7 * 86400, delta=5)

    def test_seen_too_often_key_in_active_cooldowns(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, reason_code="seen_too_often")
        active = storage.active_cooldowns("u1", time.time())
        self.assertIn("mul:6x7", active)

    def test_cooldown_expiry_not_in_active_when_past(self):
        """An expired cooldown (until in the past) must not appear in active_cooldowns."""
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        # Manually set an already-expired cooldown
        storage._cooldowns[("u1", "mul:6x7")] = time.time() - 1
        active = storage.active_cooldowns("u1", time.time())
        self.assertNotIn("mul:6x7", active)


# ── bad_problem ─────────────────────────────────────────────────────────────


class BadProblemTests(unittest.TestCase):

    def _setup_bad_problem_days(self, storage, attempt_id, day_offsets_seconds):
        """Add bad_problem feedback rows with created_at offset from now."""
        now = time.time()
        for offset in day_offsets_seconds:
            storage._bad_problem_rows.append({
                "user_id": "u1",
                "attempt_id": attempt_id,
                "reason_code": "bad_problem",
                "created_at": now + offset,
            })

    def test_one_day_of_bad_problem_does_not_exclude(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        # Two feedback rows on the SAME day (both at now)
        self._setup_bad_problem_days(storage, 1, [0, 1])
        _apply(storage, attempt_id=1, reason_code="bad_problem")
        self.assertNotIn("mul:6x7", storage.excluded_keys("u1"))

    def test_two_distinct_days_of_bad_problem_excludes(self):
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        # One row today, one row yesterday
        self._setup_bad_problem_days(storage, 1, [0, -86400])
        _apply(storage, attempt_id=1, reason_code="bad_problem")
        self.assertIn("mul:6x7", storage.excluded_keys("u1"))

    def test_bad_problem_for_different_key_does_not_exclude_this_key(self):
        """Bad problem rows for a different attempt (different key) don't count."""
        attempt = _make_primitive_attempt(attempt_id=1)
        other_attempt = _make_primitive_attempt(attempt_id=2, a=3, b=4)  # mul:3x4
        storage = _storage_with_attempt(attempt)
        storage._attempts[2] = other_attempt
        # Two days of bad_problem feedback — but on other_attempt (mul:3x4)
        now = time.time()
        for offset in [0, -86400]:
            storage._bad_problem_rows.append({
                "user_id": "u1",
                "attempt_id": 2,  # the OTHER attempt
                "reason_code": "bad_problem",
                "created_at": now + offset,
            })
        # Add one day of bad_problem on the main attempt
        storage._bad_problem_rows.append({
            "user_id": "u1",
            "attempt_id": 1,
            "reason_code": "bad_problem",
            "created_at": now,
        })
        _apply(storage, attempt_id=1, reason_code="bad_problem")
        # mul:6x7 should NOT be excluded (only 1 day)
        self.assertNotIn("mul:6x7", storage.excluded_keys("u1"))
        # mul:3x4 is not excluded either (we only called apply on attempt 1)
        self.assertNotIn("mul:3x4", storage.excluded_keys("u1"))

    def test_add_excluded_key_is_idempotent(self, tmp_path=None):
        """add_excluded_key twice for the same key must not raise or duplicate."""
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        now = time.time()
        for offset in [0, -86400]:
            storage._bad_problem_rows.append({
                "user_id": "u1",
                "attempt_id": 1,
                "reason_code": "bad_problem",
                "created_at": now + offset,
            })
        _apply(storage, attempt_id=1, reason_code="bad_problem")
        _apply(storage, attempt_id=1, reason_code="bad_problem")  # second call
        keys = storage.excluded_keys("u1")
        # still just one entry
        self.assertEqual(list(keys).count("mul:6x7"), 1)


# ── bare thumb=-1 ───────────────────────────────────────────────────────────


class BareThumbDownTests(unittest.TestCase):

    def test_thumb_down_fast_correct_primitive_grades_easy(self):
        """Primitive, correct, onset<=1200 → Easy."""
        attempt = _make_primitive_attempt(correct=True, onset_latency_ms=900)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=-1, reason_code=None)
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertIsNotNone(state)
        self.assertEqual(state["last_rating"], int(Rating.Easy))

    def test_thumb_down_slow_primitive_no_change(self):
        """Primitive, correct, but onset > 1200 → store only."""
        attempt = _make_primitive_attempt(correct=True, onset_latency_ms=1500)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=-1, reason_code=None)
        self.assertIsNone(storage.get_item_state("u1", "mul:6x7"))

    def test_thumb_down_wrong_primitive_no_change(self):
        """Primitive, wrong → store only (even if fast)."""
        attempt = _make_primitive_attempt(correct=False, onset_latency_ms=500)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=-1, reason_code=None)
        self.assertIsNone(storage.get_item_state("u1", "mul:6x7"))

    def test_thumb_down_fast_correct_compound_grades_easy(self):
        """Compound, correct, resolution <= skill target → Easy."""
        attempt = _make_compound_attempt(correct=True, resolution_latency_ms=1600)
        storage = _storage_with_attempt(attempt)
        # skill target is 1800ms (set in _storage_with_attempt)
        _apply(storage, attempt_id=2, thumb=-1, reason_code=None)
        taxon = taxonomy.classify("subtraction", {"a": 47, "b": 28})
        state = storage.get_item_state("u1", taxon.key)
        self.assertIsNotNone(state)
        self.assertEqual(state["last_rating"], int(Rating.Easy))

    def test_thumb_down_slow_compound_no_change(self):
        """Compound, correct, but resolution > skill target → store only."""
        attempt = _make_compound_attempt(correct=True, resolution_latency_ms=2500)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=2, thumb=-1, reason_code=None)
        taxon = taxonomy.classify("subtraction", {"a": 47, "b": 28})
        self.assertIsNone(storage.get_item_state("u1", taxon.key))

    def test_thumb_up_no_change(self):
        """thumb=+1 with no reason_code → store only."""
        attempt = _make_primitive_attempt(correct=True, onset_latency_ms=500)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=1, reason_code=None)
        self.assertIsNone(storage.get_item_state("u1", "mul:6x7"))


# ── reason_code wins over thumb ─────────────────────────────────────────────


class ReasonCodePrecedenceTests(unittest.TestCase):

    def test_reason_code_wins_when_both_present(self):
        """When both thumb=-1 and reason_code='too_easy', reason_code=too_easy applies
        (result is identical here, but the key semantic is reason_code is primary)."""
        attempt = _make_primitive_attempt(correct=True, onset_latency_ms=900)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=-1, reason_code="too_easy")
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertIsNotNone(state)
        self.assertEqual(state["last_rating"], int(Rating.Easy))

    def test_reason_code_seen_too_often_wins_over_thumb_down(self):
        """reason_code='seen_too_often' + thumb=-1 → cooldown action, not Easy grade."""
        attempt = _make_primitive_attempt(correct=True, onset_latency_ms=500)
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=-1, reason_code="seen_too_often")
        # Cooldown should be set
        self.assertIn("mul:6x7", storage.active_cooldowns("u1", time.time()))
        # No FSRS grade
        state = storage.get_item_state("u1", "mul:6x7")
        self.assertIsNone(state)

    def test_reason_code_too_hard_wins_over_thumb_plus(self):
        """reason_code='too_hard' → store only, despite any thumb value."""
        attempt = _make_primitive_attempt()
        storage = _storage_with_attempt(attempt)
        _apply(storage, attempt_id=1, thumb=1, reason_code="too_hard")
        self.assertIsNone(storage.get_item_state("u1", "mul:6x7"))


# ── no-op cases ─────────────────────────────────────────────────────────────


class NoOpTests(unittest.TestCase):

    def test_no_attempt_id_is_noop(self):
        storage = FakeStorage()
        _apply(storage, attempt_id=None, reason_code="too_easy")
        self.assertEqual(storage._item_states, {})

    def test_unclassifiable_attempt_is_noop(self):
        storage = FakeStorage()
        # A skill with no parameters → classify returns None
        storage._attempts[99] = {
            "id": 99, "skill_id": "mystery_skill", "parameters": {}, "user_id": "u1",
        }
        _apply(storage, attempt_id=99, reason_code="too_easy")
        self.assertEqual(storage._item_states, {})

    def test_apply_does_not_raise_on_storage_error(self):
        """apply() must never propagate exceptions."""
        class BrokenStorage:
            def get_attempt(self, _):
                raise RuntimeError("db exploded")
        # Should silently swallow the exception
        feedback_semantics.apply(BrokenStorage(), "u1", 1, None, "too_easy")


# ── storage-level tests (real SQLite) ────────────────────────────────────────


class StorageCooldownTests(unittest.TestCase):

    def _storage(self, tmp_path):
        from server.storage import Storage
        return Storage(tmp_path / "t.db")

    def test_set_and_read_cooldown(self, tmp_path=None):
        # Use FakeStorage since tmp_path isn't available in plain unittest
        storage = FakeStorage()
        now = time.time()
        storage.set_key_cooldown("u1", "mul:6x7", now + 1000)
        active = storage.active_cooldowns("u1", now)
        self.assertIn("mul:6x7", active)

    def test_upsert_cooldown_updates_until(self):
        storage = FakeStorage()
        now = time.time()
        storage.set_key_cooldown("u1", "mul:6x7", now + 1000)
        storage.set_key_cooldown("u1", "mul:6x7", now + 9999)  # override
        until = storage._cooldowns[("u1", "mul:6x7")]
        self.assertAlmostEqual(until, now + 9999, delta=5)

    def test_excluded_key_per_user(self):
        storage = FakeStorage()
        storage.add_excluded_key("u1", "mul:6x7", "bad_problem")
        self.assertIn("mul:6x7", storage.excluded_keys("u1"))
        self.assertNotIn("mul:6x7", storage.excluded_keys("u2"))

    def test_add_excluded_key_idempotent_fake(self):
        storage = FakeStorage()
        storage.add_excluded_key("u1", "mul:6x7", "bad_problem")
        storage.add_excluded_key("u1", "mul:6x7", "bad_problem")
        self.assertEqual(len(storage.excluded_keys("u1")), 1)


def test_storage_cooldown_real_db(tmp_path):
    """SQLite-backed cooldown round-trip."""
    from server.storage import Storage
    s = Storage(tmp_path / "t.db")
    user_id = s.list_users()[0]["id"]
    now = time.time()
    s.set_key_cooldown(user_id, "mul:6x7", now + 1000)
    active = s.active_cooldowns(user_id, now)
    assert "mul:6x7" in active


def test_storage_cooldown_expiry_real_db(tmp_path):
    """Expired cooldown must not appear in active_cooldowns."""
    from server.storage import Storage
    s = Storage(tmp_path / "t.db")
    user_id = s.list_users()[0]["id"]
    now = time.time()
    s.set_key_cooldown(user_id, "mul:6x7", now - 1)  # already expired
    assert "mul:6x7" not in s.active_cooldowns(user_id, now)


def test_storage_excluded_keys_real_db(tmp_path):
    """SQLite-backed excluded_keys round-trip."""
    from server.storage import Storage
    s = Storage(tmp_path / "t.db")
    user_id = s.list_users()[0]["id"]
    s.add_excluded_key(user_id, "mul:6x7", "bad_problem")
    assert "mul:6x7" in s.excluded_keys(user_id)


def test_storage_add_excluded_key_idempotent_real_db(tmp_path):
    """add_excluded_key must not raise or duplicate on repeat call."""
    from server.storage import Storage
    s = Storage(tmp_path / "t.db")
    user_id = s.list_users()[0]["id"]
    s.add_excluded_key(user_id, "mul:6x7", "bad_problem")
    s.add_excluded_key(user_id, "mul:6x7", "bad_problem")
    assert len(s.excluded_keys(user_id)) == 1


def test_storage_cooldown_upsert_real_db(tmp_path):
    """set_key_cooldown upserts (UPDATE wins) correctly."""
    from server.storage import Storage
    s = Storage(tmp_path / "t.db")
    user_id = s.list_users()[0]["id"]
    now = time.time()
    s.set_key_cooldown(user_id, "mul:6x7", now + 100)
    s.set_key_cooldown(user_id, "mul:6x7", now + 99999)
    active = s.active_cooldowns(user_id, now + 200)  # past first, within second
    assert "mul:6x7" in active


# ── scheduler integration smoke ──────────────────────────────────────────────


class FakeSchedulerStorage:
    """FakeStorage compatible with scheduler's build_session_plan, with real cooldowns."""

    def __init__(self, attempts, cooldowns=None):
        self._attempts_list = attempts
        self._cooldowns = dict(cooldowns or {})
        self._skills = {
            sid: {"id": sid, "target_latency_ms": 1800}
            for sid in ("multiplication", "division", "addition", "subtraction",
                        "money_arithmetic", "weather_math", "percent_of")
        }

    def all_attempts_for_user(self, user_id, limit=500):
        return self._attempts_list[:limit]

    def all_skill_ids(self):
        return list(self._skills)

    def get_skill(self, skill_id):
        return self._skills.get(skill_id)

    def skill_attempt_count(self, user_id, skill_id):
        return 0

    def due_items(self, user_id, now, limit=50):
        return []

    def set_key_cooldown(self, user_id, item_key, until):
        self._cooldowns[(user_id, item_key)] = until

    def active_cooldowns(self, user_id, now):
        return {k for (u, k), until in self._cooldowns.items() if u == user_id and until > now}

    def excluded_keys(self, user_id):
        return set()


def _sub_compound_attempts(n=6, a=47, b=28, correct=0, latency=6000, start=100):
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


class SchedulerIntegrationSmokeTests(unittest.TestCase):

    def test_cooldown_key_excluded_from_weak_family_slots(self):
        """After set_key_cooldown for a weak family's key, build_session_plan must
        not include that key as a weak drill slot."""
        from server.scheduler import build_session_plan

        attempts = _sub_compound_attempts()
        storage = FakeSchedulerStorage(attempts)
        now = time.time()
        taxon = taxonomy.classify("subtraction", {"a": 47, "b": 28})
        storage.set_key_cooldown("u", taxon.key, now + 7 * 86400)

        def _noop(*a, **k):
            pass

        for _ in range(10):
            plan = build_session_plan(storage, "u", 6, _noop)
            weak_keys = [s["fact_key"] for s in plan if s["role"].startswith("weak")]
            self.assertNotIn(
                taxon.key, weak_keys,
                f"Cooldown key {taxon.key!r} appeared as a weak slot",
            )


if __name__ == "__main__":
    unittest.main()
