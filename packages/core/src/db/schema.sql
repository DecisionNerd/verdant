-- Verdant operational state (jobs, settings, reviews). Blobs stay on disk.

CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT,
  error TEXT,
  cancel_requested_at TEXT,
  plan_json TEXT,
  page_progress_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES jobs (run_id) ON DELETE CASCADE,
  ts TEXT NOT NULL,
  message TEXT NOT NULL,
  phase TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_events_run_id ON job_events (run_id, id);

CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  config_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  run_id TEXT PRIMARY KEY,
  review_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
