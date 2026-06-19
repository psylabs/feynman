import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.storage import Storage


class StorageAttemptsTest(unittest.TestCase):
    def test_attempts_table_migrates_answer_mode_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "feynman.db"
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
                  prompt_audio_ms INTEGER,
                  prompt_end_ts REAL,
                  onset_ts REAL,
                  resolution_ts REAL,
                  onset_latency_ms INTEGER,
                  resolution_latency_ms INTEGER,
                  raw_transcript TEXT,
                  parsed_answer REAL,
                  expected_answer REAL NOT NULL,
                  correct INTEGER,
                  error_magnitude REAL,
                  skipped INTEGER DEFAULT 0,
                  parameters TEXT,
                  notes TEXT,
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
                """
            )
            conn.close()

            storage = Storage(path)
            sid = storage.create_session(storage.list_users()[0]["id"], "drill")
            storage.insert_attempt({
                "session_id": sid,
                "skill_id": "add",
                "position_in_session": 1,
                "prompt_text": "What is 2 plus 2?",
                "expected_answer": 4,
                "correct": True,
                "answer_mode": "typed",
            })

            attempts = storage.session_attempts(sid)
            self.assertEqual(attempts[0]["answer_mode"], "typed")


if __name__ == "__main__":
    unittest.main()
