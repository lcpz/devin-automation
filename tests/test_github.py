from typing import Any

from devin_automation.findings import _summarise_checks
from devin_automation.github import collect_pull


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
