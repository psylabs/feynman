CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,
  parent TEXT,
  display_name TEXT NOT NULL,
  tolerance_rule TEXT NOT NULL,
  target_latency_ms INTEGER NOT NULL,
  parameter_schema TEXT NOT NULL,
  templates TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  started_at REAL NOT NULL,
  ended_at REAL
);

CREATE TABLE IF NOT EXISTS attempts (
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

CREATE TABLE IF NOT EXISTS skill_state (
  skill_id TEXT PRIMARY KEY,
  rolling_accuracy REAL,
  median_latency_ms INTEGER,
  mastery REAL DEFAULT 0.5,
  last_seen_at REAL,
  attempt_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_attempts_skill ON attempts(skill_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
