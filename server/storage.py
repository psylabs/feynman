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

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        schema_path = Path(__file__).parent.parent / "schema.sql"
        with self._conn() as conn:
            conn.executescript(schema_path.read_text())

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

    # ---- skill state -------------------------------------------------------

    def init_skill_state(self, skill_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO skill_state (skill_id, mastery) VALUES (?, 0.5)",
                (skill_id,),
            )

    def prune_skills_not_in(self, keep_ids: list[str]) -> list[str]:
        """Remove skill definitions and skill_state rows for skills not in `keep_ids`.

        Attempts are kept (historical record). Returns the list of skill ids removed.
        """
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

    def get_all_skill_states(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM skill_state").fetchall()
        return [dict(r) for r in rows]

    def update_skill_state(self, skill_id: str, state: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE skill_state SET
                    rolling_accuracy = ?,
                    median_latency_ms = ?,
                    mastery = ?,
                    last_seen_at = ?,
                    attempt_count = ?
                WHERE skill_id = ?
                """,
                (
                    state.get("rolling_accuracy"),
                    state.get("median_latency_ms"),
                    state.get("mastery", 0.5),
                    state.get("last_seen_at"),
                    state.get("attempt_count", 0),
                    skill_id,
                ),
            )

    # ---- sessions ----------------------------------------------------------

    def create_session(self) -> str:
        sid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (sid, time.time()),
            )
        return sid

    def end_session(self, sid: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), sid),
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

    def recent_attempts_for_skill(self, skill_id: str, limit: int = 10) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM attempts WHERE skill_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (skill_id, limit),
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

    def get_attempt(self, attempt_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
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
                "UPDATE attempts SET notes = ? WHERE id = ?",
                (notes, attempt_id),
            )

    def skill_attempt_count(self, skill_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM attempts WHERE skill_id = ?", (skill_id,)
            ).fetchone()
        return int(row["n"]) if row else 0
