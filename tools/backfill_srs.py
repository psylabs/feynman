#!/usr/bin/env python3
"""One-time (idempotent) script: replay all historical attempts through the
FSRS rate→review→upsert pipeline to seed item_state from scratch.

For each non-skipped attempt with a classifiable taxon, the pipeline mirrors
_record_srs in server/orchestrator.py:
  classify → pick latency by tier (onset for primitives, resolution for
  compounds) → rate → review (using attempt's created_at as the review
  timestamp) → upsert_item_state.

Idempotent: deletes all item_state rows for each user found, then replays
every attempt in created_at order.  Safe to re-run.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"

# Import server modules via the project root on sys.path.
import sys
sys.path.insert(0, str(ROOT))

from server import srs, taxonomy
from server.storage import Storage


def main() -> None:
    storage = Storage(DB_PATH)

    # ── 1. Fetch all non-skipped attempts, oldest-first, with user_id + target_latency_ms ──
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            a.id,
            a.skill_id,
            a.parameters,
            a.correct,
            a.onset_latency_ms,
            a.resolution_latency_ms,
            a.created_at,
            s.user_id,
            sk.target_latency_ms
        FROM attempts a
        JOIN sessions s  ON a.session_id = s.id
        LEFT JOIN skills sk ON a.skill_id = sk.id
        WHERE a.skipped = 0
        ORDER BY a.created_at ASC
        """
    ).fetchall()
    conn.close()

    # ── 2. Collect distinct users, then wipe their item_state ──
    user_ids: set[str] = {r["user_id"] for r in rows if r["user_id"]}
    wipe_conn = sqlite3.connect(DB_PATH)
    for uid in user_ids:
        wipe_conn.execute("DELETE FROM item_state WHERE user_id = ?", (uid,))
    wipe_conn.commit()
    wipe_conn.close()

    # ── 3. Replay ──
    replayed = 0
    skipped_unclassifiable = 0

    for row in rows:
        try:
            params = json.loads(row["parameters"]) if row["parameters"] else {}
        except (TypeError, ValueError):
            params = {}

        taxon = taxonomy.classify(row["skill_id"], params)
        if taxon is None:
            skipped_unclassifiable += 1
            continue

        user_id = row["user_id"]
        correct = bool(row["correct"])

        if taxon.tier == "primitive":
            latency = row["onset_latency_ms"]
            item_target_ms = taxonomy.PRIMITIVE_ONSET_TARGET_MS
        else:
            latency = row["resolution_latency_ms"]
            item_target_ms = row["target_latency_ms"] or 5000  # fallback if skill missing

        rating = srs.rate(correct, latency, item_target_ms)

        # Use attempt's created_at as the review timestamp (tz-aware UTC).
        when = datetime.fromtimestamp(row["created_at"], tz=timezone.utc)

        existing = storage.get_item_state(user_id, taxon.key)
        card_json = existing["card_json"] if existing else None
        new_card_json, due_at = srs.review(card_json, rating, when)

        storage.upsert_item_state(
            user_id, taxon.key, taxon.tier, taxon.family,
            new_card_json, due_at, int(rating),
        )
        replayed += 1

    # ── 4. Summary ──
    summary_conn = sqlite3.connect(DB_PATH)
    summary_conn.row_factory = sqlite3.Row

    total_items = summary_conn.execute("SELECT COUNT(*) AS n FROM item_state").fetchone()["n"]
    prim_items  = summary_conn.execute(
        "SELECT COUNT(*) AS n FROM item_state WHERE tier = 'primitive'"
    ).fetchone()["n"]
    comp_items  = summary_conn.execute(
        "SELECT COUNT(*) AS n FROM item_state WHERE tier = 'compound'"
    ).fetchone()["n"]

    print(
        f"replayed {replayed} attempts into {total_items} item states "
        f"({prim_items} primitive, {comp_items} compound)"
    )
    print(f"  skipped {skipped_unclassifiable} unclassifiable attempts")

    # ── Sanity checks ──
    mul_row = summary_conn.execute(
        "SELECT lapses FROM item_state WHERE item_key = 'mul:9x12'"
    ).fetchone()
    if mul_row:
        print(f"  [OK] mul:9x12 exists, lapses={mul_row['lapses']}")
        if mul_row["lapses"] >= 1:
            print("  [OK] mul:9x12 lapses >= 1 ✓")
        else:
            print("  [WARN] mul:9x12 lapses < 1 — check attempt history")
    else:
        print("  [WARN] mul:9x12 not found in item_state")

    sub_families = summary_conn.execute(
        "SELECT DISTINCT family FROM item_state WHERE family LIKE 'sub.3d-2d%'"
    ).fetchall()
    if sub_families:
        names = [r["family"] for r in sub_families]
        print(f"  [OK] sub.3d-2d.* families present: {names}")
    else:
        print("  [WARN] no sub.3d-2d.* families found")

    min_due = summary_conn.execute("SELECT MIN(due_at) AS m FROM item_state").fetchone()["m"]
    if min_due is not None:
        min_due_dt = datetime.fromtimestamp(min_due, tz=timezone.utc)
        cutoff = datetime(2026, 5, 2, tzinfo=timezone.utc)
        if min_due_dt >= cutoff:
            print(f"  [OK] MIN(due_at) = {min_due_dt.isoformat()} >= 2026-05-02 ✓")
        else:
            print(f"  [FAIL] MIN(due_at) = {min_due_dt.isoformat()} is before 2026-05-02!")
    else:
        print("  [WARN] item_state is empty — no due_at to check")

    summary_conn.close()


if __name__ == "__main__":
    main()
