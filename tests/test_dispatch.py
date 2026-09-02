import json
from datetime import UTC, datetime
from typing import Any

import pytest

from devin_automation.devin import DevinClient
from devin_automation.dispatch import (
    DISPATCH_MARKER,
    DISPATCH_TAG,
    _dispatch_markers,
    dispatch,
)
from devin_automation.github import GitHubClient
from devin_automation.http import DEVIN_API, GITHUB_API, _iso
from devin_automation.models import Finding

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def test_dispatch_markers_are_parsed_from_hidden_comments() -> None:
    marker = {"key": "k1", "kind": "ci-failed-unattended", "session_id": "s"}
    comments = [
        {"body": "plain comment", "html_url": "u0", "created_at": "t0"},
        {
            "body": f"<!-- {DISPATCH_MARKER} {json.dumps(marker)} -->\ntext",
            "html_url": "u1",
            "created_at": "t1",
        },
        {"body": f"<!-- {DISPATCH_MARKER} not-json -->", "html_url": "u2"},
    ]
    parsed = _dispatch_markers(comments)
    assert len(parsed) == 1
    assert parsed[0]["key"] == "k1"
    assert parsed[0]["comment_url"] == "u1"


def test_dispatch_creates_session_and_idempotency_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> Any:
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/sessions"):
            return {
                "session_id": "devin-xyz",
                "url": "https://app.devin.ai/sessions/xyz",
            }
        if method == "PATCH":
            return {}
        return {"id": 1, "html_url": "https://github.com/example/project/issues/1"}

    monkeypatch.setattr("devin_automation.devin._request", fake_request)
    monkeypatch.setattr("devin_automation.github._request", fake_request)
    monkeypatch.setenv("DEVIN_CREATE_AS_USER_ID", "user-1")
    gh = GitHubClient("ghtoken", "example/project")
    devin = DevinClient("key", "org-1")
    finding = Finding(
        key="ci-failed-unattended-42-deadbeef0000",
        kind="ci-failed-unattended",
        pr_number=42,
        pr_url="https://github.com/example/project/pull/42",
        branch="devin/42-thing",
        detail="failed checks: python-lint",
        since=_iso(NOW),
    )
    done = Finding(**{**finding.__dict__, "key": "other", "dispatched": True})

    results = dispatch(gh, devin, [done, finding], dry_run=False, limit=3, max_acu=10)

    assert [r["status"] for r in results] == ["dispatched"]
    assert results[0]["session_id"] == "devin-xyz"
    method, url, body = calls[1]
    assert (method, url) == ("POST", f"{DEVIN_API}/v3/organizations/org-1/sessions")
    assert body is not None
    assert body["max_acu_limit"] == 10
    assert body["create_as_user_id"] == "user-1"
    assert DISPATCH_TAG in body["tags"]
    assert "NEVER merge" in body["prompt"]
    comment_method, comment_url, comment_body = calls[0]
    assert (comment_method, comment_url) == (
        "POST",
        f"{GITHUB_API}/repos/example/project/issues/42/comments",
    )
    assert comment_body is not None
    assert f"<!-- {DISPATCH_MARKER}" in comment_body["body"]
    assert _dispatch_markers([comment_body])[0]["key"] == finding.key
    assert calls[2][0] == "PATCH"


def test_dry_run_and_disabled_client_never_call_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network call attempted")

    monkeypatch.setattr("devin_automation.devin._request", boom)
    monkeypatch.setattr("devin_automation.github._request", boom)
    gh = GitHubClient("t", "example/project")
    disabled = DevinClient("", "")
    finding = Finding("k", "ci-failed-unattended", 1, "u", "devin/1", "d", None)

    assert dispatch(gh, disabled, [finding], dry_run=True, limit=1, max_acu=5) == [
        {"key": "k", "kind": "ci-failed-unattended", "pr": 1, "status": "dry-run"}
    ]
    assert disabled.sessions("example/project", NOW) == []
    assert disabled.automations() == []
    with pytest.raises(RuntimeError, match="not configured"):
        disabled.create_session("p", "t", [], 1)


def test_one_session_per_pr_includes_all_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> Any:
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/sessions"):
            return {"session_id": "s1", "url": "https://app.devin.ai/sessions/s1"}
        if method == "POST":
            return {"id": 7}
        return {}

    monkeypatch.setattr("devin_automation.devin._request", fake_request)
    monkeypatch.setattr("devin_automation.github._request", fake_request)
    gh = GitHubClient("t", "example/project")
    devin = DevinClient("key", "org-1")
    findings = [
        Finding(
            "review",
            "review-unaddressed",
            42,
            "u",
            "devin/42",
            "unresolved threads",
            None,
        ),
        Finding(
            "changes",
            "changes-requested-unaddressed",
            42,
            "u",
            "devin/42",
            "changes requested",
            None,
        ),
    ]
    results = dispatch(gh, devin, findings, dry_run=False, limit=3, max_acu=5)
    assert len(results) == 1
    assert results[0]["status"] == "dispatched"
    session_body = calls[1][2]
    assert session_body is not None
    assert "review-unaddressed" in session_body["prompt"]
    assert "changes-requested-unaddressed" in session_body["prompt"]


def test_failed_session_clears_marker_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
    ) -> Any:
        calls.append((method, url, body))
        if method == "POST" and url.endswith("/comments"):
            return {"id": 1}
        if method == "PATCH":
            return {}
        raise RuntimeError("create failed")

    monkeypatch.setattr("devin_automation.devin._request", fake_request)
    monkeypatch.setattr("devin_automation.github._request", fake_request)
    gh = GitHubClient("t", "example/project")
    devin = DevinClient("key", "org-1")
    finding = Finding("k", "ci-failed-unattended", 1, "u", "devin/1", "d", None)
    results = dispatch(gh, devin, [finding], dry_run=False, limit=1, max_acu=5)
    assert results[0]["status"] == "error"
    assert [call[0] for call in calls] == ["POST", "POST", "PATCH"]
    assert DISPATCH_MARKER not in (calls[2][2] or {}).get("body", "")
