"""Storage-level round-trip tests for reason_code on user_feedback."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.storage import Storage


def _minimal_schema_without_reason_code(path: Path) -> None:
    """Create a DB with the old user_feedback schema (no reason_code column)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          created_at REAL NOT NULL
        );
        CREATE TABLE skills (
          id TEXT PRIMARY KEY,
          parent TEXT,
          display_name TEXT NOT NULL,
          tolerance_rule TEXT NOT NULL,
          target_latency_ms INTEGER NOT NULL,
          parameter_schema TEXT NOT NULL,
          templates TEXT NOT NULL
        );
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          mode TEXT NOT NULL DEFAULT 'drill',
          started_at REAL NOT NULL,
          ended_at REAL
        );
        CREATE TABLE attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          skill_id TEXT NOT NULL,
          position_in_session INTEGER NOT NULL,
          prompt_text TEXT NOT NULL,
          expected_answer REAL NOT NULL,
          correct INTEGER,
          skipped INTEGER DEFAULT 0,
          created_at REAL NOT NULL
        );
        CREATE TABLE skill_state (
          user_id TEXT NOT NULL,
          skill_id TEXT NOT NULL,
          rolling_accuracy REAL,
          median_latency_ms INTEGER,
          mastery REAL DEFAULT 0.5,
          last_seen_at REAL,
          attempt_count INTEGER DEFAULT 0,
          PRIMARY KEY (user_id, skill_id)
        );
        CREATE TABLE user_feedback (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          session_id TEXT NOT NULL,
          attempt_id INTEGER,
          thumb INTEGER,
          reason TEXT,
          created_at REAL NOT NULL
        );
        """
    )
    conn.close()


class FeedbackStorageMigrationTest(unittest.TestCase):
    def test_migration_adds_reason_code_column(self):
        """Existing DB without reason_code gets the column added idempotently."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feynman.db"
            _minimal_schema_without_reason_code(path)

            # Confirm reason_code is missing before migration
            conn = sqlite3.connect(path)
            cols_before = {r[1] for r in conn.execute("PRAGMA table_info(user_feedback)").fetchall()}
            conn.close()
            self.assertNotIn("reason_code", cols_before)

            # Storage.__init__ calls _migrate which should add the column
            storage = Storage(path)

            conn = sqlite3.connect(path)
            cols_after = {r[1] for r in conn.execute("PRAGMA table_info(user_feedback)").fetchall()}
            conn.close()
            self.assertIn("reason_code", cols_after)

    def test_migration_idempotent_on_fresh_db(self):
        """A fresh DB already has reason_code; migration must not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feynman.db"
            storage = Storage(path)  # first init: creates schema + migrates
            storage2 = Storage(path)  # second init: must not fail
            self.assertIsNotNone(storage2)

    def test_insert_user_feedback_stores_reason_code(self):
        """insert_user_feedback persists reason_code and returns the row id."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feynman.db"
            storage = Storage(path)
            user_id = storage.list_users()[0]["id"]
            sid = storage.create_session(user_id, "drill")

            fid = storage.insert_user_feedback(
                user_id=user_id,
                session_id=sid,
                attempt_id=None,
                thumb=None,
                reason=None,
                reason_code="too_easy",
            )
            self.assertIsInstance(fid, int)
            self.assertGreater(fid, 0)

            # Verify the value is stored in DB
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(path)
            row = conn.execute(
                "SELECT reason_code FROM user_feedback WHERE id = ?", (fid,)
            ).fetchone()
            conn.close()
            self.assertEqual(row[0], "too_easy")

    def test_insert_user_feedback_null_reason_code(self):
        """reason_code=None is stored as NULL (backward-compat)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feynman.db"
            storage = Storage(path)
            user_id = storage.list_users()[0]["id"]
            sid = storage.create_session(user_id, "drill")

            fid = storage.insert_user_feedback(
                user_id=user_id,
                session_id=sid,
                attempt_id=None,
                thumb=1,
                reason=None,
                reason_code=None,
            )

            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(path)
            row = conn.execute(
                "SELECT reason_code FROM user_feedback WHERE id = ?", (fid,)
            ).fetchone()
            conn.close()
            self.assertIsNone(row[0])


if __name__ == "__main__":
    unittest.main()
