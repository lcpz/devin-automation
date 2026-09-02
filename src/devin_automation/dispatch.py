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

"""Idempotent Devin session dispatch for derived findings."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .devin import DevinClient
from .models import Finding, JsonDict

if TYPE_CHECKING:
    from .github import GitHubClient

DISPATCH_TAG = "devin-obs"
DISPATCH_MARKER = "devin-obs:dispatch"


def _dispatch_markers(comments: list[JsonDict]) -> list[JsonDict]:
    markers: list[JsonDict] = []
    for comment in comments:
        for match in re.finditer(
            rf"<!--\s*{DISPATCH_MARKER}\s+(\{{.*?\}})\s*-->", comment.get("body") or ""
        ):
            try:
                marker = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            marker["comment_url"] = comment.get("html_url")
            marker["created_at"] = comment.get("created_at")
            markers.append(marker)
    return markers


def dispatch_prompt(
    finding: Finding, repo: str, related: list[Finding] | None = None
) -> str:
    signals = related or [finding]
    signal_text = "\n".join(
        f"- {item.kind}: {item.detail} (since {item.since})" for item in signals
    )
    return (
        f"You were started by the periodic Devin observability job for @{repo} "
        f"(finding `{finding.key}`, kind `{finding.kind}`).\n\n"
        f"Pull request: {finding.pr_url} (branch `{finding.branch}`).\n"
        f"Signals detected for this PR:\n{signal_text}\n\n"
        "Tasks:\n"
        "1. Re-check the PR's CURRENT state first; stop with a short PR comment if the "
        "signal is already resolved, the PR is merged/closed, or another Devin session "
        "is actively working on it.\n"
        "2. For failed CI: reproduce the failing check(s) locally, fix the root "
        "cause on the same branch, push, and watch CI until green. Do not weaken "
        "or skip tests.\n"
        "3. For unresolved review threads / changes requested: address every human "
        "comment with code or a reasoned reply, reply in-thread, and resolve threads "
        "whose request you fulfilled.\n"
        "4. NEVER merge, approve, enable auto-merge or push to `master`; "
        "humans merge.\n"
        "5. Finish with one PR comment summarising what you changed and what remains."
    )


def dispatch(
    gh: GitHubClient,
    devin: DevinClient,
    findings: list[Finding],
    *,
    dry_run: bool,
    limit: int,
    max_acu: int,
) -> list[JsonDict]:
    results: list[JsonDict] = []
    priority = {
        "ci-failed-unattended": 0,
        "changes-requested-unaddressed": 1,
        "review-unaddressed": 2,
    }
    by_pr: dict[int, list[Finding]] = {}
    for finding in findings:
        by_pr.setdefault(finding.pr_number, []).append(finding)
    ordered = sorted(
        (f for f in findings if not f.dispatched),
        key=lambda f: (priority.get(f.kind, len(priority)), f.pr_number),
    )
    dispatched_prs: set[int] = set()
    for finding in ordered:
        if len(dispatched_prs) >= limit or finding.pr_number in dispatched_prs:
            continue
        record: JsonDict = {
            "key": finding.key,
            "kind": finding.kind,
            "pr": finding.pr_number,
        }
        if dry_run:
            record["status"] = "dry-run"
            dispatched_prs.add(finding.pr_number)
        else:
            marker = json.dumps({"key": finding.key, "kind": finding.kind})
            comment = gh.comment(
                finding.pr_number,
                f"<!-- {DISPATCH_MARKER} {marker} -->\n"
                "Devin observability: "
                f"`{finding.kind}` detected; a session is being started.",
            )
            dispatched_prs.add(finding.pr_number)
            try:
                session = devin.create_session(
                    prompt=dispatch_prompt(finding, gh.repo, by_pr[finding.pr_number]),
                    title=f"[obs] {finding.kind} on PR #{finding.pr_number}",
                    tags=[
                        DISPATCH_TAG,
                        f"finding:{finding.kind}",
                        f"pr:{finding.pr_number}",
                    ],
                    max_acu=max_acu,
                )
            except Exception as exc:
                gh.update_comment(
                    int(comment["id"]),
                    "Devin observability: "
                    f"dispatch failed ({exc}); the next run will retry.",
                )
                record.update({"status": "error", "error": str(exc)})
                results.append(record)
                continue
            record.update(
                {
                    "status": "dispatched",
                    "session_id": session.get("session_id"),
                    "session_url": session.get("url"),
                }
            )
            marker = json.dumps(
                {k: record[k] for k in ("key", "kind", "session_id", "session_url")}
            )
            gh.update_comment(
                int(comment["id"]),
                f"<!-- {DISPATCH_MARKER} {marker} -->\n"
                f"Devin observability: `{finding.kind}` detected ({finding.detail}); "
                f"started a session to address it: {record['session_url']}",
            )
            dispatched_prs.add(finding.pr_number)
        results.append(record)
    return results


# --------------------------------------------------------------------------- #
# Postgres sink (optional dependency)
