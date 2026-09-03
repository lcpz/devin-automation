# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Postgres schema and snapshot loader."""

from __future__ import annotations

from .findings import remediation
from .models import Snapshot

DDL = """
CREATE SCHEMA IF NOT EXISTS devin_obs;
CREATE TABLE IF NOT EXISTS devin_obs.snapshots (
  snapshot_id  BIGSERIAL PRIMARY KEY,
  collected_at TIMESTAMPTZ NOT NULL,
  repo         TEXT NOT NULL,
  source       TEXT NOT NULL,
  devin_api    BOOLEAN NOT NULL,
  pulls        INT NOT NULL,
  open_pulls   INT NOT NULL,
  failing_ci   INT NOT NULL,
  sessions     INT NOT NULL,
  acus_total   NUMERIC NOT NULL,
  findings     INT NOT NULL
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
  detail TEXT, since TIMESTAMPTZ, first_seen_at TIMESTAMPTZ, last_seen_at TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ, dispatched BOOLEAN
);
CREATE TABLE IF NOT EXISTS devin_obs.dispatches (
  key TEXT, session_id TEXT, session_url TEXT, kind TEXT, pr_number INT,
  created_at TIMESTAMPTZ, comment_url TEXT, PRIMARY KEY (key, session_id)
);
"""


def load_snapshot(database_url: str, snapshot: Snapshot, source: str) -> None:
    import psycopg2  # type: ignore[import-untyped]  # noqa: PLC0415
    from psycopg2.extras import (  # type: ignore[import-untyped]  # noqa: PLC0415
        execute_values,
    )

    now = snapshot.collected_at
    conn = psycopg2.connect(database_url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(DDL)
            cur.execute(
                """INSERT INTO devin_obs.snapshots
                   (collected_at, repo, source, devin_api, pulls, open_pulls,
                    failing_ci,
                    sessions, acus_total, findings)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    now,
                    snapshot.repo,
                    source,
                    snapshot.devin_api_enabled,
                    len(snapshot.pulls),
                    sum(p.state == "open" for p in snapshot.pulls),
                    sum(
                        p.checks == "failure" and p.state == "open"
                        for p in snapshot.pulls
                    ),
                    len(snapshot.sessions),
                    round(sum(s.acus_consumed for s in snapshot.sessions), 2),
                    len(snapshot.findings),
                ),
            )
            for pr in snapshot.pulls:
                rem = remediation(pr)
                cur.execute(
                    """INSERT INTO devin_obs.pull_requests
                       (number, title, url, author, branch, state, draft,
                        created_at, updated_at, merged_at, head_sha, last_commit_at,
                        failed_at, last_devin_comment_at, checks, failed_checks,
                        approved, changes_requested, last_human_review_at,
                        review_threads, unresolved_threads, oldest_unresolved_at,
                        remediation, last_seen_at)
                       VALUES
                       (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (number) DO UPDATE SET
                         title=EXCLUDED.title, state=EXCLUDED.state,
                         draft=EXCLUDED.draft,
                         updated_at=EXCLUDED.updated_at, merged_at=EXCLUDED.merged_at,
                         head_sha=EXCLUDED.head_sha,
                         last_commit_at=EXCLUDED.last_commit_at,
                         failed_at=EXCLUDED.failed_at,
                         last_devin_comment_at=EXCLUDED.last_devin_comment_at,
                         checks=EXCLUDED.checks, failed_checks=EXCLUDED.failed_checks,
                         approved=EXCLUDED.approved,
                         changes_requested=EXCLUDED.changes_requested,
                         last_human_review_at=EXCLUDED.last_human_review_at,
                         review_threads=EXCLUDED.review_threads,
                         unresolved_threads=EXCLUDED.unresolved_threads,
                         oldest_unresolved_at=EXCLUDED.oldest_unresolved_at,
                         remediation=EXCLUDED.remediation,
                         last_seen_at=EXCLUDED.last_seen_at""",
                    (
                        pr.number,
                        pr.title,
                        pr.url,
                        pr.author,
                        pr.branch,
                        pr.state,
                        pr.draft,
                        pr.created_at,
                        pr.updated_at,
                        pr.merged_at,
                        pr.head_sha,
                        pr.last_commit_at,
                        pr.failed_at,
                        pr.last_devin_comment_at,
                        pr.checks,
                        pr.failed_checks,
                        pr.approved,
                        pr.changes_requested,
                        pr.last_human_review_at,
                        pr.review_threads,
                        pr.unresolved_threads,
                        pr.oldest_unresolved_at,
                        rem,
                        now,
                    ),
                )
                cur.execute(
                    """INSERT INTO devin_obs.pull_request_history
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (
                        pr.number,
                        now,
                        pr.state,
                        pr.checks,
                        pr.approved,
                        pr.unresolved_threads,
                        rem,
                    ),
                )
                for d in pr.dispatches:
                    cur.execute(
                        """INSERT INTO devin_obs.dispatches
                           VALUES (%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (
                            d.get("key"),
                            d.get("session_id") or "",
                            d.get("session_url"),
                            d.get("kind"),
                            pr.number,
                            d.get("created_at"),
                            d.get("comment_url"),
                        ),
                    )
            execute_values(
                cur,
                """INSERT INTO devin_obs.check_runs VALUES %s
                   ON CONFLICT (check_run_id) DO UPDATE SET status=EXCLUDED.status,
                     conclusion=EXCLUDED.conclusion,
                     completed_at=EXCLUDED.completed_at""",
                [
                    (
                        c.check_run_id,
                        c.pr_number,
                        c.head_sha,
                        c.name,
                        c.status,
                        c.conclusion,
                        c.started_at,
                        c.completed_at,
                        c.url,
                    )
                    for c in snapshot.check_runs
                ],
            )
            for s in snapshot.sessions:
                cur.execute(
                    """INSERT INTO devin_obs.sessions VALUES
                       (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (session_id) DO UPDATE SET title=EXCLUDED.title,
                         status=EXCLUDED.status, status_detail=EXCLUDED.status_detail,
                         updated_at=EXCLUDED.updated_at,
                         acus_consumed=EXCLUDED.acus_consumed,
                         tags=EXCLUDED.tags, pr_numbers=EXCLUDED.pr_numbers,
                         category=EXCLUDED.category,
                         last_seen_at=EXCLUDED.last_seen_at""",
                    (
                        s.session_id,
                        s.title,
                        s.status,
                        s.status_detail,
                        s.origin,
                        s.automation_id,
                        s.created_at,
                        s.updated_at,
                        s.acus_consumed,
                        s.url,
                        s.tags,
                        s.pr_numbers,
                        s.category,
                        now,
                    ),
                )
            for a in snapshot.automations:
                cur.execute(
                    """INSERT INTO devin_obs.automations VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (automation_id) DO UPDATE SET name=EXCLUDED.name,
                         enabled=EXCLUDED.enabled, last_status=EXCLUDED.last_status,
                         last_fired_at=EXCLUDED.last_fired_at,
                         event_types=EXCLUDED.event_types,
                         last_seen_at=EXCLUDED.last_seen_at""",
                    (
                        a.automation_id,
                        a.name,
                        a.enabled,
                        a.last_status,
                        a.last_fired_at,
                        a.event_types,
                        now,
                    ),
                )
            open_keys = [f.key for f in snapshot.findings]
            for f in snapshot.findings:
                cur.execute(
                    """INSERT INTO devin_obs.findings VALUES
                       (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s)
                       ON CONFLICT (key) DO UPDATE SET detail=EXCLUDED.detail,
                         last_seen_at=EXCLUDED.last_seen_at, resolved_at=NULL,
                         dispatched=EXCLUDED.dispatched""",
                    (
                        f.key,
                        f.kind,
                        f.pr_number,
                        f.pr_url,
                        f.branch,
                        f.detail,
                        f.since,
                        now,
                        now,
                        f.dispatched,
                    ),
                )
            cur.execute(
                """UPDATE devin_obs.findings SET resolved_at=%s
                   WHERE resolved_at IS NULL AND NOT (key = ANY(%s))""",
                (now, open_keys),
            )
    finally:
        conn.close()
