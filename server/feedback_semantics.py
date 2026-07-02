"""Feedback semantics: apply() reads a feedback row and drives FSRS, cooldowns,
and exclusions.

Design decisions:
- reason_code WINS over thumb: when both arrive in one payload, reason_code is
  the primary action.  thumb is only consulted when reason_code is absent.
- apply() NEVER raises — it wraps _apply() in a try/except so a semantics bug
  can never break the /feedback route or the sync path.
- attempt_id=None → no-op (no item to classify).
- classify() returning None → no-op (unrecognised skill or missing parameters).
- too_hard → store only (no state change needed; weak_families already drills it).
"""

import logging
import time
from datetime import datetime, timezone

from fsrs import Rating

from server import srs, taxonomy

log = logging.getLogger(__name__)

# Primitive tier latency target (onset ≤ this → "fast").
_PRIMITIVE_ONSET_TARGET_MS = taxonomy.PRIMITIVE_ONSET_TARGET_MS  # 1200


def apply(
    storage,
    user_id: str,
    attempt_id: int | None,
    thumb: int | None,
    reason_code: str | None,
) -> None:
    """Apply feedback semantics.  Never raises; logs exceptions instead."""
    try:
        _apply(storage, user_id=user_id, attempt_id=attempt_id,
               thumb=thumb, reason_code=reason_code)
    except Exception:  # noqa: BLE001
        log.exception("feedback_semantics.apply failed (user=%s attempt=%s rc=%s)",
                      user_id, attempt_id, reason_code)


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _apply(
    storage,
    *,
    user_id: str,
    attempt_id: int | None,
    thumb: int | None,
    reason_code: str | None,
) -> None:
    # reason_code is the primary action; thumb is a fallback.
    action = reason_code

    # too_hard → store only — nothing more to do.
    if action == "too_hard":
        return

    # Bare thumb=1 (positive) or no signal at all → store only.
    if action is None and thumb != -1:
        return

    # Everything below needs an attempt to classify.
    if attempt_id is None:
        return

    attempt = storage.get_attempt(attempt_id)
    if attempt is None:
        return

    params = attempt.get("parameters") or {}
    taxon = taxonomy.classify(attempt.get("skill_id", ""), params)
    if taxon is None:
        return

    item_key = taxon.key
    now = time.time()

    if action == "too_easy":
        _grade_easy(storage, user_id, item_key, taxon, now)

    elif action == "seen_too_often":
        storage.set_key_cooldown(user_id, item_key, now + 7 * 86400)

    elif action == "bad_problem":
        _handle_bad_problem(storage, user_id, item_key)

    else:
        # action is None and thumb == -1 (bare thumbs-down with no reason_code).
        # Grade Easy ONLY when the attempt was correct and within latency target.
        if _is_fast_and_correct(attempt, taxon, storage):
            _grade_easy(storage, user_id, item_key, taxon, now)
        # else: store only.


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------


def _grade_easy(storage, user_id: str, item_key: str, taxon, now: float) -> None:
    """Run FSRS Easy on this item and persist the updated card."""
    existing = storage.get_item_state(user_id, item_key)
    card_json = existing["card_json"] if existing else None
    when = datetime.fromtimestamp(now, tz=timezone.utc)
    new_card_json, due_at = srs.review(card_json, Rating.Easy, when)
    storage.upsert_item_state(
        user_id, item_key, taxon.tier, taxon.family,
        new_card_json, due_at, int(Rating.Easy),
    )


def _handle_bad_problem(storage, user_id: str, item_key: str) -> None:
    """Tally distinct days of bad_problem for item_key; exclude when ≥ 2."""
    days = _count_bad_problem_days(storage, user_id, item_key)
    if days >= 2:
        storage.add_excluded_key(user_id, item_key, "bad_problem")


def _count_bad_problem_days(storage, user_id: str, item_key: str) -> int:
    """Count DISTINCT UTC calendar days on which this item_key received a
    bad_problem rating from this user.

    Implementation: fetch all bad_problem feedback rows for the user, classify
    each row's attempt, and keep only those whose taxon.key matches item_key.
    Volume is tiny (personal app), so the Python loop is fine.
    """
    rows = storage.bad_problem_feedback_for_user(user_id)
    days: set[str] = set()
    for row in rows:
        aid = row.get("attempt_id")
        if aid is None:
            continue
        attempt = storage.get_attempt(aid)
        if attempt is None:
            continue
        params = attempt.get("parameters") or {}
        taxon = taxonomy.classify(attempt.get("skill_id", ""), params)
        if taxon is not None and taxon.key == item_key:
            ts = row.get("created_at") or 0
            day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            days.add(day)
    return len(days)


def _is_fast_and_correct(attempt: dict, taxon, storage) -> bool:
    """True when the attempt was correct AND within its tier-appropriate latency.

    Primitive tier: onset_latency_ms ≤ PRIMITIVE_ONSET_TARGET_MS (1200 ms).
    Compound tier:  resolution_latency_ms ≤ skill's target_latency_ms.
    """
    if not attempt.get("correct"):
        return False

    if taxon.tier == "primitive":
        onset = attempt.get("onset_latency_ms")
        return onset is not None and onset <= _PRIMITIVE_ONSET_TARGET_MS

    # compound
    resolution = attempt.get("resolution_latency_ms")
    if resolution is None:
        return False
    skill = storage.get_skill(attempt.get("skill_id", ""))
    if skill is None:
        return False
    return resolution <= skill.get("target_latency_ms", 0)
