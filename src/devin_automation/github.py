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

"""GitHub REST and GraphQL client plus PR collection."""

from __future__ import annotations

import os
import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

from .devin import DevinClient
from .dispatch import DISPATCH_MARKER, _dispatch_markers
from .findings import IGNORED_CHECKS, _summarise_checks, derive_findings
from .http import GITHUB_API, GITHUB_GRAPHQL, _iso, _parse_ts, _request
from .models import AutomationRow, CheckRun, JsonDict, PullRow, SessionRow, Snapshot

BOT_LOGINS = {"devin-ai-integration[bot]", "github-actions[bot]"}
DEVIN_BRANCH_PREFIX = "devin/"
LOOKBACK = timedelta(days=int(os.environ.get("DEVIN_OBS_LOOKBACK_DAYS", "60")))
ACTIVE_SESSION_STATES = {"new", "claimed", "running", "resuming", "working"}
FAILED_SESSION_STATES = {"expired", "failed", "cancelled"}


class GitHubClient:
    def __init__(self, token: str, repo: str) -> None:
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _paginate(self, path: str, params: dict[str, str] | None = None) -> list[Any]:
        params = {"per_page": "100", **(params or {})}
        page, results = 1, []
        while True:
            query = urllib.parse.urlencode({**params, "page": str(page)})
            data = _request("GET", f"{GITHUB_API}{path}?{query}", self.headers)
            if not data:
                break
            results.extend(data)
            if len(data) < 100:
                break
            page += 1
        return results

    def pulls(self, since: datetime) -> list[JsonDict]:
        pulls = self._paginate(
            f"/repos/{self.repo}/pulls",
            {"state": "all", "sort": "updated", "direction": "desc"},
        )
        return [p for p in pulls if (_parse_ts(p["updated_at"]) or since) >= since]

    def check_runs(self, sha: str) -> list[JsonDict]:
        data = _request(
            "GET",
            f"{GITHUB_API}/repos/{self.repo}/commits/{sha}/check-runs?per_page=100",
            self.headers,
        )
        return list(data.get("check_runs", []))

    def reviews(self, number: int) -> list[JsonDict]:
        return self._paginate(f"/repos/{self.repo}/pulls/{number}/reviews")

    def issue_comments(self, number: int) -> list[JsonDict]:
        return self._paginate(f"/repos/{self.repo}/issues/{number}/comments")

    def commits(self, number: int) -> list[JsonDict]:
        return self._paginate(f"/repos/{self.repo}/pulls/{number}/commits")

    def review_threads(self, number: int) -> list[JsonDict]:
        """Review threads with resolution state (REST does not expose it)."""
        query = """
        query($owner:String!,$name:String!,$number:Int!,$after:String){
          repository(owner:$owner,name:$name){ pullRequest(number:$number){
            reviewThreads(first:100, after:$after){
              pageInfo{hasNextPage endCursor}
              nodes{ id isResolved isOutdated path
                comments(last:1){ nodes{ author{login} createdAt url } } } } } } }
        """
        threads: list[JsonDict] = []
        after: str | None = None
        while True:
            data = _request(
                "POST",
                GITHUB_GRAPHQL,
                self.headers,
                {
                    "query": query,
                    "variables": {
                        "owner": self.owner,
                        "name": self.name,
                        "number": number,
                        "after": after,
                    },
                },
            )
            if data.get("errors"):
                raise RuntimeError(f"GraphQL: {data['errors']}")
            conn = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            threads.extend(conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                return threads
            after = conn["pageInfo"]["endCursor"]

    def comment(self, number: int, body: str) -> JsonDict:
        result: JsonDict = _request(
            "POST",
            f"{GITHUB_API}/repos/{self.repo}/issues/{number}/comments",
            self.headers,
            {"body": body},
        )
        return result

    def update_comment(self, comment_id: int, body: str) -> JsonDict:
        result: JsonDict = _request(
            "PATCH",
            f"{GITHUB_API}/repos/{self.repo}/issues/comments/{comment_id}",
            self.headers,
            {"body": body},
        )
        return result


def _pr_numbers(repo: str, text: str) -> list[int]:
    return sorted(
        {
            int(m.group(1))
            for m in re.finditer(
                rf"https://github\.com/{re.escape(repo)}/pull/(\d+)", text or ""
            )
        }
    )


def collect_pull(gh: GitHubClient, pull: JsonDict) -> tuple[PullRow, list[CheckRun]]:
    number = pull["number"]
    sha = pull["head"]["sha"]
    runs = gh.check_runs(sha)
    checks, failed = _summarise_checks(runs)
    check_rows = [
        CheckRun(
            check_run_id=int(r["id"]),
            pr_number=number,
            head_sha=sha,
            name=r["name"],
            status=r.get("status", ""),
            conclusion=r.get("conclusion"),
            started_at=r.get("started_at"),
            completed_at=r.get("completed_at"),
            url=r.get("html_url"),
        )
        for r in runs
    ]
    latest: dict[str, tuple[str, str]] = {}
    for review in gh.reviews(number):
        login = (review.get("user") or {}).get("login", "")
        if login in BOT_LOGINS:
            continue
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[login] = (review["state"], review.get("submitted_at", ""))
    states = {s for s, _ in latest.values()}
    human_review_at = max((ts for _, ts in latest.values()), default=None)
    threads = gh.review_threads(number)
    unresolved = [
        t
        for t in threads
        if not t["isResolved"]
        and not t["isOutdated"]
        and ((t["comments"]["nodes"] or [{}])[0].get("author") or {}).get("login")
        not in BOT_LOGINS
    ]
    oldest = min(
        (
            t["comments"]["nodes"][0]["createdAt"]
            for t in unresolved
            if t["comments"]["nodes"]
        ),
        default=None,
    )
    commits = gh.commits(number)
    last_commit_at = max(
        (c["commit"]["committer"]["date"] for c in commits), default=None
    )
    issue_comments = gh.issue_comments(number)
    last_devin_comment_at = max(
        (
            c["created_at"]
            for c in issue_comments
            if (c.get("user") or {}).get("login") == "devin-ai-integration[bot]"
            and DISPATCH_MARKER not in (c.get("body") or "")
        ),
        default=None,
    )
    failed_at_values = [
        completed_at
        for r in runs
        if r.get("status") == "completed"
        and r.get("name") not in IGNORED_CHECKS
        and r.get("conclusion") not in {"success", "skipped", "neutral"}
        and isinstance(completed_at := r.get("completed_at"), str)
    ]
    row = PullRow(
        number=number,
        title=pull["title"],
        url=pull["html_url"],
        author=(pull.get("user") or {}).get("login", ""),
        branch=pull["head"]["ref"],
        state="merged" if pull.get("merged_at") else pull.get("state", "open"),
        draft=bool(pull.get("draft")),
        created_at=pull["created_at"],
        updated_at=pull["updated_at"],
        merged_at=pull.get("merged_at"),
        head_sha=sha,
        last_commit_at=last_commit_at,
        failed_at=max(failed_at_values, default=None),
        last_devin_comment_at=last_devin_comment_at,
        checks=checks,
        failed_checks=failed,
        approved="APPROVED" in states and "CHANGES_REQUESTED" not in states,
        changes_requested="CHANGES_REQUESTED" in states,
        last_human_review_at=human_review_at or None,
        review_threads=len(threads),
        unresolved_threads=len(unresolved),
        oldest_unresolved_at=oldest,
        dispatches=_dispatch_markers(issue_comments),
    )
    return row, check_rows


def collect(gh: GitHubClient, devin: DevinClient, now: datetime) -> Snapshot:
    since = now - LOOKBACK
    pulls: list[PullRow] = []
    checks: list[CheckRun] = []
    for pull in gh.pulls(since):
        if not pull["head"]["ref"].startswith(DEVIN_BRANCH_PREFIX):
            continue
        row, runs = collect_pull(gh, pull)
        pulls.append(row)
        checks.extend(runs)

    sessions = [
        SessionRow(
            session_id=s["session_id"],
            title=s.get("title"),
            status=s.get("status"),
            status_detail=s.get("status_detail"),
            origin=s.get("origin"),
            automation_id=s.get("automation_id"),
            created_at=s.get("created_at"),
            updated_at=s.get("updated_at"),
            acus_consumed=float(s.get("acus_consumed") or 0),
            url=s.get("url"),
            tags=list(s.get("tags") or []),
            pr_numbers=sorted(
                {
                    n
                    for pr in s.get("pull_requests") or []
                    for n in _pr_numbers(gh.repo, pr.get("pr_url", ""))
                }
            ),
            category=s.get("category"),
        )
        for s in devin.sessions(gh.repo, since)
    ]
    automations = []
    for a in devin.automations():
        last = a.get("last_invocation") or {}
        fired = last.get("fired_at")
        automations.append(
            AutomationRow(
                automation_id=a["automation_id"],
                name=a.get("name", ""),
                enabled=bool(a.get("enabled")),
                last_status=last.get("status"),
                last_fired_at=_iso(datetime.fromtimestamp(fired, tz=UTC))
                if isinstance(fired, (int, float))
                else fired,
                event_types=[t.get("event_type", "") for t in a.get("triggers") or []],
            )
        )
    snapshot = Snapshot(
        collected_at=_iso(now) or "",
        repo=gh.repo,
        devin_api_enabled=devin.enabled,
        pulls=pulls,
        check_runs=checks,
        sessions=sessions,
        automations=automations,
        findings=[],
    )
    snapshot.findings = derive_findings(snapshot, now)
    return snapshot


# --------------------------------------------------------------------------- #
