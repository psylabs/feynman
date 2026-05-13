"""Seed correct synthetic attempts for fact-keys the user already knows cold.

Why this script exists: Feynman's scheduler de-prioritises by fact-key (see
``server/diagnosis.fact_key`` and ``drill_priorities``), not by a manual
"mastered" flag on the skill row. To stop drilling easy facts (multiplication
by 10, addition/subtraction with operands <3) before the user has accumulated
enough natural attempts on them, we insert a handful of perfect, fast attempts
so the existing diagnosis logic recognises them as mastered.

The seeded attempts are tagged ``notes='seed:mastered'`` so they can be
identified, kept idempotent across reruns, and rolled back with::

    DELETE FROM attempts WHERE notes = 'seed:mastered';
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.storage import Storage  # noqa: E402
from server import mastery  # noqa: E402


SEED_NOTE = "seed:mastered"
SEED_LATENCY_MS = 1500


def _multiplication_pairs() -> list[tuple[int, int]]:
    pairs = [(n, 10) for n in range(1, 11)]
    pairs += [(10, 11), (10, 12)]
    return pairs


def _small_addition_pairs() -> list[tuple[int, int]]:
    return [(0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)]


def _small_subtraction_pairs() -> list[tuple[int, int]]:
    return [(0, 0), (1, 0), (1, 1), (2, 0), (2, 1), (2, 2)]


def _build_attempts(skill_id: str) -> list[dict]:
    out: list[dict] = []
    if skill_id == "multiplication":
        for a, b in _multiplication_pairs():
            out.append({
                "prompt_text": f"What is {a} times {b}?",
                "expected": a * b,
                "params": {"a": a, "b": b},
            })
    elif skill_id == "addition":
        for a, b in _small_addition_pairs():
            out.append({
                "prompt_text": f"What is {a} plus {b}?",
                "expected": a + b,
                "params": {"a": a, "b": b, "carry": False},
            })
    elif skill_id == "subtraction":
        for a, b in _small_subtraction_pairs():
            out.append({
                "prompt_text": f"What is {a} minus {b}?",
                "expected": a - b,
                "params": {"a": a, "b": b, "borrow": False},
            })
    return out


def _already_seeded(db_path: Path, user_id: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM attempts a JOIN sessions s ON a.session_id = s.id "
            "WHERE s.user_id = ? AND a.notes = ? LIMIT 1",
            (user_id, SEED_NOTE),
        ).fetchone()
    return row is not None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="Username (matches users.name)")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-seed even if seed:mastered attempts already exist",
    )
    args = ap.parse_args()

    storage = Storage(ROOT / "data" / "feynman.db")

    user = next(
        (u for u in storage.list_users() if u["name"] == args.user),
        None,
    )
    if not user:
        print(f"error: no user named {args.user!r}", file=sys.stderr)
        return 2

    if _already_seeded(storage.db_path, user["id"]) and not args.force:
        print(f"already seeded for {args.user!r}; pass --force to re-seed")
        return 0

    skills = ("multiplication", "addition", "subtraction")
    session_id = storage.create_session(user["id"], mode="seed")
    print(f"seeding into session {session_id}")

    pos = 0
    for skill_id in skills:
        records = _build_attempts(skill_id)
        for rec in records:
            pos += 1
            storage.insert_attempt({
                "session_id": session_id,
                "skill_id": skill_id,
                "position_in_session": pos,
                "prompt_text": rec["prompt_text"],
                "prompt_end_ts": time.time(),
                "onset_ts": time.time(),
                "resolution_ts": time.time(),
                "resolution_latency_ms": SEED_LATENCY_MS,
                "parsed_answer": rec["expected"],
                "expected_answer": rec["expected"],
                "correct": True,
                "skipped": False,
                "parameters": rec["params"],
                "notes": SEED_NOTE,
            })
        print(f"  {skill_id}: seeded {len(records)} attempts")

    storage.end_session(session_id)

    def _emit(_event, **_kwargs):
        return None

    for skill_id in skills:
        skill = storage.get_skill(skill_id)
        if not skill:
            continue
        mastery.update(
            storage,
            user["id"],
            skill_id,
            skill["target_latency_ms"],
            _emit,
        )
    print("done. mastery state recomputed for affected skills.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
