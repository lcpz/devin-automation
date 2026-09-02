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

"""Command-line interface for collection, findings, dispatch, and loading."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from .db import load_snapshot
from .devin import DevinClient
from .dispatch import dispatch
from .github import GitHubClient, collect
from .models import (
    AutomationRow,
    CheckRun,
    Finding,
    JsonDict,
    PullRow,
    SessionRow,
    Snapshot,
)


def _snapshot_from_json(data: JsonDict) -> Snapshot:
    return Snapshot(
        collected_at=data["collected_at"],
        repo=data["repo"],
        devin_api_enabled=data["devin_api_enabled"],
        pulls=[PullRow(**p) for p in data["pulls"]],
        check_runs=[CheckRun(**c) for c in data["check_runs"]],
        sessions=[SessionRow(**s) for s in data["sessions"]],
        automations=[AutomationRow(**a) for a in data["automations"]],
        findings=[Finding(**f) for f in data["findings"]],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "findings"):
        p = sub.add_parser(name)
        p.add_argument("--json", help="write result to this file")
        p.add_argument(
            "--database-url", default=os.environ.get("DEVIN_OBS_DATABASE_URL")
        )
        p.add_argument("--source", default=os.environ.get("DEVIN_OBS_SOURCE", "cli"))
    p = sub.add_parser("dispatch")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=3)
    p.add_argument("--max-acu", type=int, default=15)
    p.add_argument("--json", help="write dispatch records to this file")
    p = sub.add_parser("load")
    p.add_argument("snapshot")
    p.add_argument("--database-url", default=os.environ.get("DEVIN_OBS_DATABASE_URL"))
    p.add_argument("--source", default=os.environ.get("DEVIN_OBS_SOURCE", "artifact"))
    args = parser.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        parser.error("GITHUB_REPOSITORY is required (owner/repository)")
    gh = GitHubClient(os.environ.get("GITHUB_TOKEN", ""), repo)
    devin = DevinClient(
        os.environ.get("DEVIN_API_KEY", ""), os.environ.get("DEVIN_ORG_ID", "")
    )
    now = datetime.now(UTC)

    if args.command == "load":
        with open(args.snapshot, encoding="utf-8") as fh:
            snapshot = _snapshot_from_json(json.load(fh))
        if not args.database_url:
            parser.error("--database-url / DEVIN_OBS_DATABASE_URL required for load")
        load_snapshot(args.database_url, snapshot, args.source)
        print(
            f"loaded snapshot {snapshot.collected_at} into "
            f"{args.database_url.split('@')[-1]}"
        )
        return 0

    snapshot = collect(gh, devin, now)

    if args.command == "dispatch":
        records = dispatch(
            gh,
            devin,
            snapshot.findings,
            dry_run=args.dry_run,
            limit=args.max,
            max_acu=args.max_acu,
        )
        output = json.dumps(records, indent=2)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                fh.write(output)
        print(output)
        return 0

    payload: Any = (
        asdict(snapshot)
        if args.command == "collect"
        else [asdict(f) for f in snapshot.findings]
    )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
    if args.database_url and args.command == "collect":
        load_snapshot(args.database_url, snapshot, args.source)
    summary = {
        "collected_at": snapshot.collected_at,
        "devin_api": snapshot.devin_api_enabled,
        "pulls": len(snapshot.pulls),
        "open": sum(p.state == "open" for p in snapshot.pulls),
        "failing_ci": sum(
            p.checks == "failure" and p.state == "open" for p in snapshot.pulls
        ),
        "sessions": len(snapshot.sessions),
        "automations": len(snapshot.automations),
        "findings": [
            f"{f.kind}#{f.pr_number}{' (dispatched)' if f.dispatched else ''}"
            for f in snapshot.findings
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
