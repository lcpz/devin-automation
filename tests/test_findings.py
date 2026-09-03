from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from devin_automation.cli import _snapshot_from_json
from devin_automation.db import DDL, load_snapshot
from devin_automation.findings import derive_findings, remediation
from devin_automation.http import _iso
from devin_automation.models import PullRow, SessionRow, Snapshot

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _pr(**overrides: Any) -> PullRow:
    base: dict[str, Any] = {
        "number": 42,
        "title": "feat: thing",
        "url": "https://github.com/example/project/pull/42",
        "author": "devin-ai-integration[bot]",
        "branch": "devin/42-thing",
        "state": "open",
        "draft": False,
        "created_at": _iso(NOW - timedelta(days=1)),
        "updated_at": _iso(NOW),
        "merged_at": None,
        "head_sha": "abc123",
        "last_commit_at": _iso(NOW - timedelta(hours=5)),
        "failed_at": None,
        "last_devin_comment_at": None,
        "checks": "success",
        "failed_checks": [],
        "approved": False,
        "changes_requested": False,
        "last_human_review_at": None,
        "review_threads": 0,
        "unresolved_threads": 0,
        "oldest_unresolved_at": None,
    }
    base.update(overrides)
    return PullRow(**base)


def _snapshot(
    pulls: list[PullRow], sessions: list[SessionRow] | None = None
) -> Snapshot:
    return Snapshot(
        collected_at=_iso(NOW) or "",
        repo="example/project",
        devin_api_enabled=bool(sessions),
        pulls=pulls,
        check_runs=[],
        sessions=sessions or [],
        automations=[],
        findings=[],
    )


def test_failed_ci_without_session_is_a_finding_once() -> None:
    pr = _pr(checks="failure", failed_checks=["python-lint"])
    findings = derive_findings(_snapshot([pr]), NOW)
    assert [f.kind for f in findings] == ["ci-failed-unattended"]
    assert findings[0].pr_number == 42
    assert not findings[0].dispatched

    pr.dispatches = [{"key": findings[0].key, "session_id": "s1"}]
    again = derive_findings(_snapshot([pr]), NOW)
    assert again[0].dispatched


def test_recent_commit_or_active_session_suppresses_ci_finding() -> None:
    fresh = _pr(checks="failure", last_commit_at=_iso(NOW - timedelta(minutes=10)))
    assert derive_findings(_snapshot([fresh]), NOW) == []

    stale = _pr(checks="failure")
    session = SessionRow(
        session_id="s1",
        title="Fix CI on PR #42",
        status="running",
        status_detail=None,
        origin="automation",
        automation_id="auto-1",
        created_at=_iso(NOW),
        updated_at=_iso(NOW),
        acus_consumed=1.0,
        url="https://app.devin.ai/sessions/s1",
        tags=[],
        pr_numbers=[42],
        category="fix",
    )
    assert derive_findings(_snapshot([stale], [session]), NOW) == []


def test_review_findings_require_no_follow_up_commit() -> None:
    review_at = _iso(NOW - timedelta(hours=4))
    pr = _pr(
        unresolved_threads=2,
        oldest_unresolved_at=review_at,
        changes_requested=True,
        last_human_review_at=review_at,
        last_commit_at=_iso(NOW - timedelta(hours=6)),
    )
    kinds = sorted(f.kind for f in derive_findings(_snapshot([pr]), NOW))
    assert kinds == ["changes-requested-unaddressed", "review-unaddressed"]

    pr.last_commit_at = _iso(NOW - timedelta(hours=3))
    assert derive_findings(_snapshot([pr]), NOW) == []

    closed = _pr(state="merged", unresolved_threads=1, oldest_unresolved_at=review_at)
    assert derive_findings(_snapshot([closed]), NOW) == []


def test_failed_check_age_not_commit_age() -> None:
    recent_failure = _pr(
        checks="failure",
        failed_checks=["python-lint"],
        failed_at=_iso(NOW - timedelta(minutes=10)),
    )
    assert derive_findings(_snapshot([recent_failure]), NOW) == []

    old_failure = _pr(
        checks="failure",
        failed_checks=["python-lint"],
        failed_at=_iso(NOW - timedelta(hours=5)),
    )
    assert [f.kind for f in derive_findings(_snapshot([old_failure]), NOW)] == [
        "ci-failed-unattended"
    ]


def test_session_state_controls_suppression() -> None:
    pr = _pr(checks="failure")
    for status, expected in [
        ("working", []),
        ("failed", ["ci-failed-unattended"]),
        ("finished", []),
    ]:
        session = SessionRow(
            session_id="s1",
            title="Fix CI on PR #42",
            status=status,
            status_detail=None,
            origin="automation",
            automation_id="auto-1",
            created_at=_iso(NOW),
            updated_at=_iso(NOW),
            acus_consumed=1.0,
            url="https://app.devin.ai/sessions/s1",
            tags=[],
            pr_numbers=[42],
            category="fix",
        )
        assert [
            f.kind for f in derive_findings(_snapshot([pr], [session]), NOW)
        ] == expected


def test_provisional_dispatch_marker_expires_but_completed_marker_does_not() -> None:
    pr = _pr(checks="failure", failed_checks=["python-lint"])
    finding = derive_findings(_snapshot([pr]), NOW)[0]

    pr.dispatches = [
        {
            "key": finding.key,
            "created_at": _iso(NOW - timedelta(minutes=10)),
        }
    ]
    assert derive_findings(_snapshot([pr]), NOW)[0].dispatched

    pr.dispatches[0]["created_at"] = _iso(NOW - timedelta(hours=5))
    assert not derive_findings(_snapshot([pr]), NOW)[0].dispatched

    pr.dispatches[0] = {"key": finding.key, "session_id": "session-1"}
    assert derive_findings(_snapshot([pr]), NOW)[0].dispatched


def test_recent_devin_progress_comment_suppresses_finding() -> None:
    recent = _pr(
        checks="failure",
        failed_checks=["python-lint"],
        last_devin_comment_at=_iso(NOW - timedelta(hours=1)),
    )
    assert derive_findings(_snapshot([recent]), NOW) == []

    old = _pr(
        checks="failure",
        failed_checks=["python-lint"],
        last_devin_comment_at=_iso(NOW - timedelta(hours=6)),
    )
    assert [f.kind for f in derive_findings(_snapshot([old]), NOW)] == [
        "ci-failed-unattended"
    ]


def test_old_snapshot_defaults_new_pull_activity_fields() -> None:
    pull = asdict(_pr())
    pull.pop("failed_at")
    pull.pop("last_devin_comment_at")
    snapshot = _snapshot_from_json(
        {
            "collected_at": _iso(NOW),
            "repo": "example/project",
            "devin_api_enabled": False,
            "pulls": [pull],
            "check_runs": [],
            "sessions": [],
            "automations": [],
            "findings": [],
        }
    )
    assert snapshot.pulls[0].failed_at is None
    assert snapshot.pulls[0].last_devin_comment_at is None


def test_pull_request_insert_and_schema_support_activity_column(
    monkeypatch: Any,
) -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, Any]] = []

        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def execute(self, query: str, params: Any = None) -> None:
            self.executed.append((query, params))

    class FakeConnection:
        def __init__(self, cursor: FakeCursor) -> None:
            self.cursor_instance = cursor

        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def cursor(self) -> FakeCursor:
            return self.cursor_instance

        def close(self) -> None:
            return None

    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    psycopg2 = type("FakePsycopg2", (), {})()
    psycopg2.connect = lambda database_url: connection

    class FakeExtras:
        @staticmethod
        def execute_values(*args: Any) -> None:
            return None

    monkeypatch.setitem(__import__("sys").modules, "psycopg2", psycopg2)
    monkeypatch.setitem(__import__("sys").modules, "psycopg2.extras", FakeExtras)

    load_snapshot("postgresql://unused", _snapshot([_pr()]), "test")

    assert "ADD COLUMN IF NOT EXISTS last_devin_comment_at TIMESTAMPTZ" in DDL
    pull_insert = next(
        query
        for query, _ in cursor.executed
        if "INSERT INTO devin_obs.pull_requests" in query
    )
    assert "last_devin_comment_at" in pull_insert.split("VALUES", 1)[0]


def test_remediation_classification() -> None:
    assert remediation(_pr(state="merged")) == "merged"
    assert remediation(_pr(state="closed")) == "closed"
    assert remediation(_pr(checks="failure")) == "failed-ci"
    assert remediation(_pr(approved=True)) == "ready-to-merge"
    assert remediation(_pr(unresolved_threads=1)) == "awaiting-devin"
    assert remediation(_pr()) == "awaiting-review"
    assert remediation(_pr(checks="pending")) == "ci-pending"
