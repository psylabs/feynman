"""Shared helper for consuming entries from the LLM-generated template pool.

The pool lives at POOL_PATH (default data/template_pool.json).  Point a test
at a different file by monkeypatching forge_pool.POOL_PATH before calling
take().
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
POOL_PATH: Path = ROOT / "data" / "template_pool.json"

# Keys beyond these indicate the scheduler wants something specific a canned entry can't honor.
_BASE_TARGET_KEYS = frozenset({"operation", "level"})


def eligible(target: dict | None) -> bool:
    """Return False when target contains keys beyond 'operation' and 'level'.

    A pinned target (e.g., people=4, location hint) means the scheduler wants
    something specific that a canned forge entry can't honor; skip the pool.
    """
    if not target:
        return True
    return not bool(target.keys() - _BASE_TARGET_KEYS)


def take(
    skill_id: str,
    operation: str,
    validator: Optional[Callable[[dict], bool]] = None,
) -> Optional[dict]:
    """Return and consume the first unused pool entry matching skill_id+operation.

    Marks the entry used=True and writes the file back atomically (write to a
    sibling .tmp file + os.replace) so concurrent readers always see a
    consistent snapshot.

    ``validator``, when given, is called on each candidate entry before it is
    served. An entry that fails the validator is marked ``{"used": True,
    "invalid": True}`` and the scan continues to the next candidate — so one
    bad LLM-forged entry doesn't block the whole operation, it just gets
    burned. All markings (invalid skips plus the final served entry, if any)
    are flushed in a single atomic write-back, same as before.

    Returns None when:
    - the pool file is missing or unreadable/unparseable
    - no unused entry matches the requested skill_id and operation
    - every matching entry fails the validator
    """
    pool_path = Path(POOL_PATH)
    try:
        raw = pool_path.read_text()
        entries = json.loads(raw)
        if not isinstance(entries, list):
            return None
    except (OSError, json.JSONDecodeError):
        return None

    updated = list(entries)
    changed = False
    served: Optional[dict] = None

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        if entry.get("used"):
            continue
        if entry.get("skill_id") != skill_id:
            continue
        if entry.get("operation") != operation:
            continue

        if validator is not None and not validator(entry):
            updated[i] = {**entry, "used": True, "invalid": True}
            changed = True
            logger.warning(
                "forge_pool: entry %s failed validator, marking invalid "
                "(skill_id=%s operation=%s)",
                entry.get("id"), skill_id, operation,
            )
            continue

        # Found a match — mark it used; write-back happens once, below.
        updated[i] = {**entry, "used": True}
        changed = True
        served = entry  # return the original (used=False) so caller has full entry
        break

    if changed:
        tmp_path = pool_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(updated, indent=2))
            os.replace(str(tmp_path), str(pool_path))
        except OSError as exc:
            logger.warning("forge_pool: failed to write pool back: %s", exc)

    return served
