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

"""Check summarisation, finding derivation, and remediation classification."""

from __future__ import annotations

import functools
import hashlib
import os
from datetime import datetime, timedelta
from typing import Any

from .http import _parse_ts
from .models import Finding, PullRow, SessionRow, Snapshot

JsonDict = dict[str, Any]

UNATTENDED_GRACE = timedelta(hours=int(os.environ.get("DEVIN_OBS_GRACE_HOURS", "2")))
IGNORED_CHECKS = set(
    filter(
        None, os.environ.get("DEVIN_OBS_IGNORE_CHECKS", "actions-timeline").split(",")
    )
)
ACTIVE_SESSION_STATES = {"new", "claimed", "running", "resuming", "working"}
FAILED_SESSION_STATES = {"expired", "failed", "cancelled"}


def _summarise_checks(runs: list[JsonDict]) -> tuple[str, list[str]]:
    runs = [r for r in runs if r.get("name") not in IGNORED_CHECKS]
    if not runs:
        return "none", []
    if any(r.get("status") != "completed" for r in runs):
        return "pending", []
    failed = [
        r["name"]
        for r in runs
        if r.get("conclusion") not in {"success", "skipped", "neutral"}
    ]
    return ("failure", failed) if failed else ("success", [])


def _finding_key(kind: str, pr: PullRow, anchor: str) -> str:
    digest = hashlib.sha1(f"{kind}:{pr.number}:{anchor}".encode()).hexdigest()[:12]  # noqa: S324
    return f"{kind}-{pr.number}-{digest}"


def _session_active_since(
    sessions: list[SessionRow], pr: PullRow, since: datetime
) -> bool:
    for s in sessions:
        if pr.number in s.pr_numbers or f"#{pr.number}" in (s.title or ""):
            updated = _parse_ts(s.updated_at)
            status = (s.status or "").lower()
            if status in ACTIVE_SESSION_STATES:
                return True
            if updated and updated >= since and status not in FAILED_SESSION_STATES:
                return True
    return False


def _activity_since(sessions: list[SessionRow], pr: PullRow, since: datetime) -> bool:
    comment_at = _parse_ts(pr.last_devin_comment_at)
    return _session_active_since(sessions, pr, since) or (
        comment_at is not None and comment_at >= since
    )


def _add_finding(
    findings: list[Finding],
    pr: PullRow,
    kind: str,
    anchor: str,
    detail: str,
    since: str | None,
    now: datetime,
) -> None:
    key = _finding_key(kind, pr, anchor)
    dispatched = False
    for marker in pr.dispatches:
        if marker.get("key") != key:
            continue
        if marker.get("session_id"):
            dispatched = True
            break
        try:
            created_at = _parse_ts(marker.get("created_at"))
        except ValueError:
            continue
        if created_at and created_at >= now - UNATTENDED_GRACE:
            dispatched = True
            break
    findings.append(
        Finding(
            key=key,
            kind=kind,
            pr_number=pr.number,
            pr_url=pr.url,
            branch=pr.branch,
            detail=detail,
            since=since,
            dispatched=dispatched,
        )
    )


def derive_findings(snapshot: Snapshot, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    for pr in snapshot.pulls:
        if pr.state != "open" or pr.draft:
            continue
        add = functools.partial(_add_finding, findings, pr, now=now)
        last_commit = _parse_ts(pr.last_commit_at) or _parse_ts(pr.created_at) or now
        quiet_since = now - UNATTENDED_GRACE
        failed_at = _parse_ts(pr.failed_at)
        ci_since = max(last_commit, failed_at) if failed_at else last_commit

        if (
            pr.checks == "failure"
            and ci_since <= quiet_since
            and not _activity_since(snapshot.sessions, pr, ci_since)
        ):
            add(
                "ci-failed-unattended",
                pr.head_sha,
                f"failed checks: {', '.join(pr.failed_checks[:5])}",
                pr.failed_at or pr.last_commit_at,
            )
        oldest = _parse_ts(pr.oldest_unresolved_at)
        if (
            pr.unresolved_threads
            and oldest
            and oldest <= quiet_since
            and last_commit < oldest
            and not _activity_since(snapshot.sessions, pr, oldest)
        ):
            add(
                "review-unaddressed",
                pr.oldest_unresolved_at or "",
                f"{pr.unresolved_threads} unresolved human review thread(s)",
                pr.oldest_unresolved_at,
            )
        review_at = _parse_ts(pr.last_human_review_at)
        if (
            pr.changes_requested
            and review_at
            and review_at <= quiet_since
            and last_commit < review_at
            and not _activity_since(snapshot.sessions, pr, review_at)
        ):
            add(
                "changes-requested-unaddressed",
                pr.last_human_review_at or "",
                "changes requested with no follow-up commit",
                pr.last_human_review_at,
            )
    return findings


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


def remediation(pr: PullRow) -> str:
    """Delivery state stronger than CI: green CI + human approval / merge."""
    if pr.state == "merged":
        return "merged"
    if pr.state == "closed":
        return "closed"
    if pr.checks == "failure":
        return "failed-ci"
    if pr.checks == "success" and pr.approved:
        return "ready-to-merge"
    if pr.changes_requested or pr.unresolved_threads:
        return "awaiting-devin"
    if pr.checks == "success":
        return "awaiting-review"
    return "ci-pending"
