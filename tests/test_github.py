from datetime import UTC, datetime
from typing import Any

from devin_automation.cli import _snapshot_from_json
from devin_automation.findings import _summarise_checks
from devin_automation.github import _session_row, collect_pull
from devin_automation.http import _iso, _parse_ts


def test_summarise_checks_ignores_informational_and_flags_failures() -> None:
    runs: list[dict[str, Any]] = [
        {"name": "python-lint", "status": "completed", "conclusion": "success"},
        {"name": "actions-timeline", "status": "queued", "conclusion": None},
    ]
    assert _summarise_checks(runs) == ("success", [])
    runs.append({"name": "docs", "status": "completed", "conclusion": "failure"})
    assert _summarise_checks(runs) == ("failure", ["docs"])
    runs.append({"name": "e2e", "status": "in_progress", "conclusion": None})
    assert _summarise_checks(runs) == ("pending", [])
    assert _summarise_checks([]) == ("none", [])


def test_parse_epoch_timestamp_and_iso_round_trip() -> None:
    expected = datetime(2026, 9, 3, 11, 49, 11, tzinfo=UTC)
    parsed = _parse_ts(1788436151)

    assert parsed == expected
    assert _parse_ts(_iso(parsed)) == expected


def test_session_row_normalizes_epoch_timestamps() -> None:
    row = _session_row(
        "lcpz/superset",
        {
            "session_id": "session-1",
            "created_at": 1788436151,
            "updated_at": 1788436152,
        },
    )

    assert row.created_at == "2026-09-03T11:49:11+00:00"
    assert row.updated_at == "2026-09-03T11:49:12+00:00"


def test_snapshot_from_json_normalizes_epoch_timestamps() -> None:
    snapshot = _snapshot_from_json(
        {
            "collected_at": "2026-09-03T12:00:00+00:00",
            "repo": "lcpz/superset",
            "devin_api_enabled": True,
            "pulls": [],
            "check_runs": [],
            "sessions": [
                {
                    "session_id": "session-1",
                    "title": None,
                    "status": None,
                    "status_detail": None,
                    "origin": None,
                    "automation_id": None,
                    "created_at": 1788436151,
                    "updated_at": 1788436152,
                    "acus_consumed": 0.0,
                    "url": None,
                    "tags": [],
                    "pr_numbers": [14, 15],
                    "category": None,
                }
            ],
            "automations": [],
            "findings": [],
        }
    )

    assert snapshot.sessions[0].created_at == "2026-09-03T11:49:11+00:00"
    assert snapshot.sessions[0].updated_at == "2026-09-03T11:49:12+00:00"
    assert snapshot.sessions[0].pr_numbers == [14, 15]


def test_deleted_review_author_is_counted_as_human() -> None:
    class FakeGitHub:
        def __init__(self) -> None:
            self.comments_calls = 0

        def check_runs(self, sha: str) -> list[dict[str, Any]]:
            return []

        def reviews(self, number: int) -> list[dict[str, Any]]:
            return []

        def review_threads(self, number: int) -> list[dict[str, Any]]:
            return [
                {
                    "isResolved": False,
                    "isOutdated": False,
                    "comments": {"nodes": [{"author": None, "createdAt": "t"}]},
                }
            ]

        def commits(self, number: int) -> list[dict[str, Any]]:
            return []

        def issue_comments(self, number: int) -> list[dict[str, Any]]:
            self.comments_calls += 1
            return [
                {
                    "body": '<!-- devin-obs:dispatch {"key": "marker"} -->',
                    "created_at": "2026-09-02T00:00:00+00:00",
                    "html_url": "https://github.com/c/1",
                    "user": {"login": "devin-ai-integration[bot]"},
                }
            ]

    github = FakeGitHub()
    row, _ = collect_pull(
        github,
        {
            "number": 42,
            "title": "feat",
            "html_url": "https://github.com/example/project/pull/42",
            "user": {"login": "human"},
            "head": {"ref": "devin/42-feat", "sha": "abc"},
            "state": "open",
            "draft": False,
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
            "merged_at": None,
        },
    )
    assert row.unresolved_threads == 1
    assert row.last_devin_comment_at is None
    assert github.comments_calls == 1
