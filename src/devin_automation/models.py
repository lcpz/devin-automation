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

"""Dataclasses representing collected automation observability data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass
class CheckRun:
    check_run_id: int
    pr_number: int
    head_sha: str
    name: str
    status: str
    conclusion: str | None
    started_at: str | None
    completed_at: str | None
    url: str | None


@dataclass
class PullRow:
    number: int
    title: str
    url: str
    author: str
    branch: str
    state: str  # open | merged | closed
    draft: bool
    created_at: str
    updated_at: str
    merged_at: str | None
    head_sha: str
    last_commit_at: str | None
    failed_at: str | None
    last_devin_comment_at: str | None
    checks: str  # success | failure | pending | none
    failed_checks: list[str]
    approved: bool
    changes_requested: bool
    last_human_review_at: str | None
    review_threads: int
    unresolved_threads: int
    oldest_unresolved_at: str | None
    dispatches: list[JsonDict] = field(default_factory=list)


@dataclass
class SessionRow:
    session_id: str
    title: str | None
    status: str | None
    status_detail: str | None
    origin: str | None
    automation_id: str | None
    created_at: str | None
    updated_at: str | None
    acus_consumed: float
    url: str | None
    tags: list[str]
    pr_numbers: list[int]
    category: str | None


@dataclass
class AutomationRow:
    automation_id: str
    name: str
    enabled: bool
    last_status: str | None
    last_fired_at: str | None
    event_types: list[str]


@dataclass
class Finding:
    key: str
    kind: str  # ci-failed-unattended|review-unaddressed|changes-requested-unaddressed
    pr_number: int
    pr_url: str
    branch: str
    detail: str
    since: str | None
    dispatched: bool = False


@dataclass
class Snapshot:
    collected_at: str
    repo: str
    devin_api_enabled: bool
    pulls: list[PullRow]
    check_runs: list[CheckRun]
    sessions: list[SessionRow]
    automations: list[AutomationRow]
    findings: list[Finding]


# --------------------------------------------------------------------------- #
