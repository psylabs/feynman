import json
import sqlite3
import time
import uuid
from pathlib import Path


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        schema_path = Path(__file__).parent.parent / "schema.sql"
        with self._conn() as conn:
            conn.executescript(schema_path.read_text())

    def _migrate(self) -> None:
        """Idempotent migration to bring older databases onto the multi-user schema."""
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
            if not row:
                default_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                    (default_id, "Mo", time.time()),
                )
            else:
                default_id = row["id"]

            session_cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "user_id" not in session_cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            if "mode" not in session_cols:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'drill'"
                )
            conn.execute(
                "UPDATE sessions SET user_id = ? WHERE user_id IS NULL",
                (default_id,),
            )
            conn.execute("UPDATE sessions SET mode = 'drill' WHERE mode IS NULL")

            ss_cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(skill_state)").fetchall()
            }
            if "user_id" not in ss_cols:
                conn.executescript(
                    """
                    CREATE TABLE skill_state_new (
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
                conn.execute(
                    """
                    INSERT INTO skill_state_new
                        (user_id, skill_id, rolling_accuracy, median_latency_ms,
                         mastery, last_seen_at, attempt_count)
                    SELECT ?, skill_id, rolling_accuracy, median_latency_ms,
                           mastery, last_seen_at, attempt_count
                    FROM skill_state
                    """,
                    (default_id,),
                )
                conn.executescript(
                    """
                    DROP TABLE skill_state;
                    ALTER TABLE skill_state_new RENAME TO skill_state;
                    """
                )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)"
            )

    # ---- users -------------------------------------------------------------

    def list_users(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at FROM users ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def create_user(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("name cannot be empty")
        uid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                (uid, name, time.time()),
            )
        return {"id": uid, "name": name}

    def get_user(self, user_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def has_completed_eval(self, user_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE user_id = ? AND mode = 'eval' "
                "AND ended_at IS NOT NULL LIMIT 1",
                (user_id,),
            ).fetchone()
        return row is not None

    # ---- skills ------------------------------------------------------------

    def upsert_skill(self, skill: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO skills (id, parent, display_name, tolerance_rule,
                                    target_latency_ms, parameter_schema, templates)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    parent=excluded.parent,
                    display_name=excluded.display_name,
                    tolerance_rule=excluded.tolerance_rule,
                    target_latency_ms=excluded.target_latency_ms,
                    parameter_schema=excluded.parameter_schema,
                    templates=excluded.templates
                """,
                (
                    skill["id"],
                    skill.get("parent"),
                    skill["display_name"],
                    json.dumps(skill["tolerance"]),
                    skill["target_latency_ms"],
                    json.dumps(skill.get("parameter_schema", {})),
                    json.dumps(skill.get("templates", [])),
                ),
            )

    def get_skill(self, skill_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM skills WHERE id = ?", (skill_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tolerance"] = json.loads(d.pop("tolerance_rule"))
        d["parameter_schema"] = json.loads(d["parameter_schema"])
        d["templates"] = json.loads(d["templates"])
        return d

    def all_skill_ids(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id FROM skills").fetchall()
        return [r["id"] for r in rows]

    # ---- skill state -------------------------------------------------------

    def init_skill_state(self, user_id: str, skill_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO skill_state (user_id, skill_id, mastery) "
                "VALUES (?, ?, 0.5)",
                (user_id, skill_id),
            )

    def ensure_skill_states_for_user(self, user_id: str) -> None:
        for skill_id in self.all_skill_ids():
            self.init_skill_state(user_id, skill_id)

    def get_all_skill_states(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM skill_state WHERE user_id = ?", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def update_skill_state(self, user_id: str, skill_id: str, state: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE skill_state SET
                    rolling_accuracy = ?,
                    median_latency_ms = ?,
                    mastery = ?,
                    last_seen_at = ?,
                    attempt_count = ?
                WHERE user_id = ? AND skill_id = ?
                """,
                (
                    state.get("rolling_accuracy"),
                    state.get("median_latency_ms"),
                    state.get("mastery", 0.5),
                    state.get("last_seen_at"),
                    state.get("attempt_count", 0),
                    user_id,
                    skill_id,
                ),
            )

    def prune_skills_not_in(self, keep_ids: list[str]) -> list[str]:
        if not keep_ids:
            return []
        placeholders = ",".join("?" * len(keep_ids))
        with self._conn() as conn:
            removed = [
                r["id"]
                for r in conn.execute(
                    f"SELECT id FROM skills WHERE id NOT IN ({placeholders})",
                    keep_ids,
                ).fetchall()
            ]
            conn.execute(
                f"DELETE FROM skills WHERE id NOT IN ({placeholders})", keep_ids
            )
            conn.execute(
                f"DELETE FROM skill_state WHERE skill_id NOT IN ({placeholders})",
                keep_ids,
            )
        return removed

    # ---- sessions ----------------------------------------------------------

    def create_session(self, user_id: str, mode: str = "drill") -> str:
        sid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, mode, started_at) VALUES (?, ?, ?, ?)",
                (sid, user_id, mode, time.time()),
            )
        return sid

    def get_session(self, sid: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (sid,)
            ).fetchone()
        return dict(row) if row else None

    def end_session(self, sid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (time.time(), sid)
            )

    def session_attempt_count(self, sid: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE session_id = ?", (sid,)
            ).fetchone()
        return int(row["n"]) if row else 0

    def session_attempts(self, sid: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT a.*, s.display_name AS skill_name
                FROM attempts a
                LEFT JOIN skills s ON a.skill_id = s.id
                WHERE a.session_id = ?
                ORDER BY a.position_in_session ASC
                """,
                (sid,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- attempts ----------------------------------------------------------

    def insert_attempt(self, a: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO attempts (
                    session_id, skill_id, position_in_session, prompt_text, prompt_audio_ms,
                    prompt_end_ts, onset_ts, resolution_ts, onset_latency_ms, resolution_latency_ms,
                    raw_transcript, parsed_answer, expected_answer, correct, error_magnitude,
                    skipped, parameters, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a["session_id"],
                    a["skill_id"],
                    a["position_in_session"],
                    a["prompt_text"],
                    a.get("prompt_audio_ms"),
                    a.get("prompt_end_ts"),
                    a.get("onset_ts"),
                    a.get("resolution_ts"),
                    a.get("onset_latency_ms"),
                    a.get("resolution_latency_ms"),
                    a.get("raw_transcript"),
                    a.get("parsed_answer"),
                    a["expected_answer"],
                    int(bool(a["correct"])) if a.get("correct") is not None else None,
                    a.get("error_magnitude"),
                    int(bool(a.get("skipped", False))),
                    json.dumps(a.get("parameters", {})),
                    a.get("notes"),
                    time.time(),
                ),
            )
            return int(cur.lastrowid)

    def recent_attempts_for_skill(
        self, user_id: str, skill_id: str, limit: int = 10
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT a.* FROM attempts a
                JOIN sessions s ON a.session_id = s.id
                WHERE a.skill_id = ? AND s.user_id = ?
                ORDER BY a.created_at DESC LIMIT ?
                """,
                (skill_id, user_id, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("parameters"):
                try:
                    d["parameters"] = json.loads(d["parameters"])
                except (TypeError, ValueError):
                    pass
            out.append(d)
        return out

    def skill_attempt_count(self, user_id: str, skill_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM attempts a
                JOIN sessions s ON a.session_id = s.id
                WHERE a.skill_id = ? AND s.user_id = ?
                """,
                (skill_id, user_id),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_attempt(self, attempt_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT a.*, s.user_id FROM attempts a "
                "JOIN sessions s ON a.session_id = s.id "
                "WHERE a.id = ?",
                (attempt_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("parameters"):
            try:
                d["parameters"] = json.loads(d["parameters"])
            except (TypeError, ValueError):
                pass
        return d

    def update_attempt_notes(self, attempt_id: int, notes: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE attempts SET notes = ? WHERE id = ?", (notes, attempt_id)
            )
