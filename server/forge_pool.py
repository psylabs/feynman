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
from typing import Optional

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


def take(skill_id: str, operation: str) -> Optional[dict]:
    """Return and consume the first unused pool entry matching skill_id+operation.

    Marks the entry used=True and writes the file back atomically (write to a
    sibling .tmp file + os.replace) so concurrent readers always see a
    consistent snapshot.

    Returns None when:
    - the pool file is missing or unreadable/unparseable
    - no unused entry matches the requested skill_id and operation
    """
    pool_path = Path(POOL_PATH)
    try:
        raw = pool_path.read_text()
        entries = json.loads(raw)
        if not isinstance(entries, list):
            return None
    except (OSError, json.JSONDecodeError):
        return None

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        if entry.get("used"):
            continue
        if entry.get("skill_id") != skill_id:
            continue
        if entry.get("operation") != operation:
            continue

        # Found a match — mark it used and write back atomically.
        updated = list(entries)
        updated[i] = {**entry, "used": True}
        tmp_path = pool_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(updated, indent=2))
            os.replace(str(tmp_path), str(pool_path))
        except OSError as exc:
            logger.warning("forge_pool: failed to write pool back: %s", exc)

        return entry  # return the original (used=False) so caller has full entry

    return None
