#!/usr/bin/env python3
"""Backfill reason_code onto historical user_feedback rows and replay semantics.

### What this does

1. **Keyword mapping** (case-insensitive, first-match-wins) over the `reason`
   text of rows where reason_code IS NULL:
     - contains `simple`, `easy`, or `trivial` → `too_easy`
     - contains `frequent`, `repet`, or `just asked` → `seen_too_often`
     - contains `hard` → `too_hard`
     - else → leave NULL, PRINT the row for manual review
   Rows with thumb only and NO reason text are replayed once as bare-thumb, then
   marked with the DB-only sentinel reason_code='thumb_only'.  The sentinel is
   NOT a valid API reason code — server/main.py and server/sync.py are untouched.
   Note: feedback_semantics.apply() with reason_code='thumb_only' would fall into
   its bare-thumb else-branch and misbehave; the replay loop guards against this
   by skipping apply() for any 'thumb_only' row.

2. **Replay** feedback_semantics.apply() in chronological created_at order for:
   - (default) Only the rows whose reason_code was NULL at script start
     (i.e. rows just coded + bare-thumb rows without a reason).
   - (--replay-all) Every feedback row regardless of prior state, including
     rows already marked 'thumb_only' (apply() is still guarded for them).

### Stale-cooldown rule

`seen_too_often` sets a cooldown of `now + 7 days`. Replaying a month-old
"seen_too_often" row would create a cooldown starting TODAY, which is stale.
Resolution: if a `seen_too_often` row's `created_at` is more than 7 days ago
(created_at < now - 7*86400), we SKIP the apply() call for that row entirely
and count it as "stale, skipped". Easy-grade and exclusion writes are
idempotent and reasonable to replay; only cooldown skipping is special-cased.

### Idempotency

Safe to re-run:
  - Coding step: filtered on `reason_code IS NULL`, so already-coded rows are
    never re-matched or re-updated.
  - Replay step (default, without --replay-all): only replays rows that were
    NULL at script start.  After the first run, bare-thumb rows are marked
    'thumb_only' (reason_code no longer NULL), so re-running finds no NULL
    bare-thumb rows, codes nothing for them, and replays nothing — a true no-op
    including bare-thumb rows.
  - With --replay-all: Easy re-grades compound on re-run (apply()'s own note
    says as much); use this flag deliberately, not in an automated loop.
    'thumb_only' sentinel rows are included in the fetch but apply() is not
    called for them (guarded in the replay loop), so they do not compound.

### Usage

    uv run python tools/backfill_feedback_codes.py
    uv run python tools/backfill_feedback_codes.py --replay-all
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "feynman.db"

sys.path.insert(0, str(ROOT))

from server import feedback_semantics  # noqa: E402
from server.storage import Storage  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEVEN_DAYS = 7 * 86400

_MAPPINGS = [
    # (keywords_to_check, reason_code_to_assign)
    (["simple", "easy", "trivial"], "too_easy"),
    (["frequent", "repet", "just asked"], "seen_too_often"),
    (["hard"], "too_hard"),
]


def _classify_reason(reason: str | None) -> str | None:
    """Return the matched reason_code for the given free-text reason, or None."""
    if not reason:
        return None
    lower = reason.lower()
    for keywords, code in _MAPPINGS:
        if any(kw in lower for kw in keywords):
            return code
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay-all",
        action="store_true",
        help="Replay apply() for EVERY feedback row, not just those coded today",
    )
    args = parser.parse_args()

    now = time.time()

    # ── 1. Fetch all rows where reason_code IS NULL ──────────────────────────
    # 'thumb_only' sentinel rows are automatically excluded here since their
    # reason_code is no longer NULL.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Ensure reason_code column exists (Storage migration); init then close.
    storage = Storage(DB_PATH)

    null_rows = conn.execute(
        """
        SELECT id, user_id, attempt_id, thumb, reason, created_at
        FROM user_feedback
        WHERE reason_code IS NULL
        ORDER BY created_at ASC
        """
    ).fetchall()

    print(f"Found {len(null_rows)} rows with reason_code IS NULL.")

    # ── 2. Keyword mapping ───────────────────────────────────────────────────
    assignments: dict[int, str] = {}   # row_id → new reason_code
    unmatched: list[sqlite3.Row] = []  # rows with reason but no match

    for row in null_rows:
        reason = row["reason"]
        if not reason:
            # Bare-thumb row — will be replayed then marked 'thumb_only'.
            continue
        code = _classify_reason(reason)
        if code is not None:
            assignments[row["id"]] = code
        else:
            unmatched.append(row)

    # ── 3. UPDATE ────────────────────────────────────────────────────────────
    if assignments:
        for row_id, code in assignments.items():
            conn.execute(
                "UPDATE user_feedback SET reason_code = ? WHERE id = ?",
                (code, row_id),
            )
        conn.commit()
        print(f"Updated {len(assignments)} rows with new reason_codes.")
    else:
        print("No rows needed coding (all already coded or bare-thumb).")

    # ── 4. Print unmatched rows for manual review ────────────────────────────
    if unmatched:
        print(f"\n⚠  {len(unmatched)} row(s) left NULL (manual review needed):")
        for row in unmatched:
            print(f"  id={row['id']}  reason={row['reason']!r}")
    else:
        print("All reason-bearing rows were successfully coded.")

    # ── 5. Determine rows to replay ──────────────────────────────────────────
    if args.replay_all:
        replay_rows = conn.execute(
            """
            SELECT id, user_id, attempt_id, thumb, reason, reason_code, created_at
            FROM user_feedback
            ORDER BY created_at ASC
            """
        ).fetchall()
        print(f"\nReplay mode: --replay-all → {len(replay_rows)} rows.")
    else:
        # Only replay rows that were NULL at script start (just coded + bare-thumb).
        null_ids = {r["id"] for r in null_rows}
        replay_rows = conn.execute(
            f"""
            SELECT id, user_id, attempt_id, thumb, reason, reason_code, created_at
            FROM user_feedback
            WHERE id IN ({','.join('?' for _ in null_ids)})
            ORDER BY created_at ASC
            """,
            list(null_ids),
        ).fetchall() if null_ids else []
        print(f"\nDefault replay mode: {len(replay_rows)} rows (just-coded + bare-thumb).")

    conn.close()

    # ── 6. Replay feedback_semantics.apply() ─────────────────────────────────
    counts: dict[str, int] = {
        "too_easy": 0,
        "seen_too_often": 0,
        "too_hard": 0,
        "bare_thumb": 0,
        "thumb_only_sentinel": 0,  # already-processed bare-thumb rows; apply() skipped
        "stale_skipped": 0,
        "no_op": 0,  # NULL reason_code + no thumb or thumb=+1
    }

    # IDs of truly bare-thumb rows (rc=None, thumb=-1, no reason text) that were
    # replayed this run and should be marked with the 'thumb_only' sentinel.
    bare_thumb_replayed: list[int] = []

    for row in replay_rows:
        rc = row["reason_code"]
        thumb = row["thumb"]
        created_at = row["created_at"]

        # 'thumb_only' sentinel: bare-thumb row already processed on a prior run.
        # apply() with reason_code='thumb_only' would misbehave (falls into the
        # bare-thumb else-branch); skip it here rather than modifying the module.
        if rc == "thumb_only":
            counts["thumb_only_sentinel"] += 1
            continue

        # Stale seen_too_often: skip cooldown entirely.
        if rc == "seen_too_often" and (now - created_at) > _SEVEN_DAYS:
            counts["stale_skipped"] += 1
            continue

        # Count the semantic bucket before calling apply().
        if rc == "too_easy":
            counts["too_easy"] += 1
        elif rc == "seen_too_often":
            counts["seen_too_often"] += 1
        elif rc == "too_hard":
            counts["too_hard"] += 1
        elif rc is None and thumb == -1:
            counts["bare_thumb"] += 1
            # Collect truly bare-thumb rows (no reason text) for sentinel marking.
            if not row["reason"]:
                bare_thumb_replayed.append(row["id"])
        else:
            counts["no_op"] += 1
            # Positive thumb or NULL/NULL → store only; skip apply() call.
            continue

        feedback_semantics.apply(
            storage,
            user_id=row["user_id"],
            attempt_id=row["attempt_id"],
            thumb=thumb,
            reason_code=rc,
        )

    # ── 6b. Mark bare-thumb rows with 'thumb_only' sentinel ─────────────────
    # This prevents them from appearing in null_rows on subsequent default runs,
    # making re-runs a true no-op for bare-thumb rows.
    if bare_thumb_replayed:
        sentinel_conn = sqlite3.connect(DB_PATH)
        for row_id in bare_thumb_replayed:
            sentinel_conn.execute(
                "UPDATE user_feedback SET reason_code = 'thumb_only' WHERE id = ?",
                (row_id,),
            )
        sentinel_conn.commit()
        sentinel_conn.close()
        print(f"Marked {len(bare_thumb_replayed)} bare-thumb rows with sentinel 'thumb_only'.")

    # ── 7. Summary table ─────────────────────────────────────────────────────
    # Re-read final counts from DB.
    summary_conn = sqlite3.connect(DB_PATH)
    summary_conn.row_factory = sqlite3.Row

    total_rows = summary_conn.execute("SELECT COUNT(*) AS n FROM user_feedback").fetchone()["n"]
    coded = summary_conn.execute(
        "SELECT reason_code, COUNT(*) AS n FROM user_feedback "
        "WHERE reason_code IS NOT NULL GROUP BY reason_code"
    ).fetchall()
    still_null = summary_conn.execute(
        "SELECT COUNT(*) AS n FROM user_feedback WHERE reason_code IS NULL"
    ).fetchone()["n"]
    summary_conn.close()

    print("\n── Final DB state ──────────────────────────────────────────────────")
    print(f"  Total feedback rows : {total_rows}")
    for row in coded:
        print(f"  {row['reason_code']:<20}: {row['n']}")
    print(f"  {'NULL (no code)':<20}: {still_null}")

    print("\n── Replay effects ──────────────────────────────────────────────────")
    print(f"  too_easy applied    : {counts['too_easy']}  (→ FSRS Easy grade)")
    print(f"  seen_too_often      : {counts['seen_too_often']}  (→ cooldown set)")
    print(f"  too_hard            : {counts['too_hard']}  (→ store only, no-op)")
    print(f"  bare_thumb=-1       : {counts['bare_thumb']}  (→ grade Easy if fast+correct)")
    print(f"  thumb_only (sentinel): {counts['thumb_only_sentinel']}  (→ skipped, already processed)")
    print(f"  stale_skipped       : {counts['stale_skipped']}  (seen_too_often >7d old, cooldown skipped)")
    print(f"  no_op (skipped)     : {counts['no_op']}  (positive thumb / no signal)")

    # Newly coded rows breakdown
    print("\n── Newly coded breakdown ───────────────────────────────────────────")
    code_counts: dict[str, int] = {}
    for row_id, code in assignments.items():
        code_counts[code] = code_counts.get(code, 0) + 1
    if code_counts:
        for code, n in sorted(code_counts.items()):
            print(f"  {code:<20}: {n}")
    else:
        print("  (none — all rows were already coded)")
    null_reason_count = len(unmatched)
    print(f"  {'unmatched (NULL)':<20}: {null_reason_count}")


if __name__ == "__main__":
    main()
