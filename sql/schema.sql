CREATE SCHEMA IF NOT EXISTS devin_obs;
CREATE TABLE IF NOT EXISTS devin_obs.snapshots (
  snapshot_id BIGSERIAL PRIMARY KEY,
  collected_at TIMESTAMPTZ NOT NULL,
  repo TEXT NOT NULL,
  source TEXT NOT NULL,
  devin_api BOOLEAN NOT NULL,
  pulls INT NOT NULL,
  open_pulls INT NOT NULL,
  failing_ci INT NOT NULL,
  sessions INT NOT NULL,
  acus_total NUMERIC NOT NULL,
  findings INT NOT NULL
);
CREATE TABLE IF NOT EXISTS devin_obs.pull_requests (
  number INT PRIMARY KEY, title TEXT, url TEXT, author TEXT, branch TEXT,
  state TEXT, draft BOOLEAN, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
  merged_at TIMESTAMPTZ, head_sha TEXT, last_commit_at TIMESTAMPTZ,
  failed_at TIMESTAMPTZ, last_devin_comment_at TIMESTAMPTZ,
  checks TEXT, failed_checks TEXT[],
  approved BOOLEAN, changes_requested BOOLEAN,
  last_human_review_at TIMESTAMPTZ, review_threads INT, unresolved_threads INT,
  oldest_unresolved_at TIMESTAMPTZ, remediation TEXT, last_seen_at TIMESTAMPTZ
);
ALTER TABLE devin_obs.pull_requests
  ADD COLUMN IF NOT EXISTS last_devin_comment_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS devin_obs.pull_request_history (
  number INT, collected_at TIMESTAMPTZ, state TEXT, checks TEXT,
  approved BOOLEAN, unresolved_threads INT, remediation TEXT,
  PRIMARY KEY (number, collected_at)
);
CREATE TABLE IF NOT EXISTS devin_obs.check_runs (
  check_run_id BIGINT PRIMARY KEY, pr_number INT, head_sha TEXT, name TEXT,
  status TEXT, conclusion TEXT, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
  url TEXT
);
CREATE TABLE IF NOT EXISTS devin_obs.sessions (
  session_id TEXT PRIMARY KEY, title TEXT, status TEXT, status_detail TEXT,
  origin TEXT, automation_id TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
  acus_consumed NUMERIC, url TEXT, tags TEXT[], pr_numbers INT[], category TEXT,
  last_seen_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS devin_obs.automations (
  automation_id TEXT PRIMARY KEY, name TEXT, enabled BOOLEAN, last_status TEXT,
  last_fired_at TIMESTAMPTZ, event_types TEXT[], last_seen_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS devin_obs.findings (
  key TEXT PRIMARY KEY, kind TEXT, pr_number INT, pr_url TEXT, branch TEXT,
  detail TEXT, since TIMESTAMPTZ, first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ, dispatched BOOLEAN
);
CREATE TABLE IF NOT EXISTS devin_obs.dispatches (
  key TEXT, session_id TEXT, session_url TEXT, kind TEXT, pr_number INT,
  created_at TIMESTAMPTZ, comment_url TEXT, PRIMARY KEY (key, session_id)
);
