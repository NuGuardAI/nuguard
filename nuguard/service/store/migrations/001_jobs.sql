-- Remote scan service job store (SQLite). Secrets are never stored here:
-- request_json excludes github_token; has_token only records that one was given.
CREATE TABLE IF NOT EXISTS scan_jobs (
  id               TEXT PRIMARY KEY,
  owner_id         TEXT NOT NULL,
  status           TEXT NOT NULL,
  phase            TEXT,
  repo_url         TEXT NOT NULL,            -- sanitized
  ref_requested    TEXT,
  commit_sha       TEXT,
  client_reference TEXT,
  request_json     TEXT NOT NULL,            -- ScanCreateRequest minus github_token
  request_sha256   TEXT NOT NULL,            -- for idempotency (token excluded)
  idempotency_key  TEXT,
  has_token        INTEGER NOT NULL DEFAULT 0,
  error_json       TEXT,
  result_ref       TEXT,                     -- "inline" or a file path
  result_sha256    TEXT,
  created_at       TEXT NOT NULL,
  started_at       TEXT,
  finished_at      TEXT,
  expires_at       TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_owner_created ON scan_jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON scan_jobs(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idem
  ON scan_jobs(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS scan_results (
  job_id      TEXT PRIMARY KEY REFERENCES scan_jobs(id) ON DELETE CASCADE,
  result_json TEXT NOT NULL,
  sbom_json   TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  ts             TEXT NOT NULL,
  owner_id       TEXT,
  action         TEXT NOT NULL,              -- create|cancel|read_result|auth_fail
  job_id         TEXT,
  repo_url       TEXT,
  remote_addr    TEXT,
  request_sha256 TEXT
);
