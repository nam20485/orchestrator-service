from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from webhook_receiver.app import create_app
from webhook_receiver.config import Settings


def _test_settings() -> Settings:
    repo = Path(__file__).resolve().parent.parent
    return Settings(
        host="127.0.0.1",
        port=8080,
        github_webhook_secret="test-webhook-secret",
        opencode_server_url="http://localhost:4099",
        prompt_script=repo / "scripts" / "prompt.ps1",
        workspace="/workspace",
        model="zai-coding-plan/glm-4.7-flash",
        agent="orchestrator",
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    return TestClient(create_app(_test_settings()))


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ping(client: TestClient) -> None:
    body = b'{"zen":"test"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-ping",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pong"


def test_rejects_bad_signature(client: TestClient) -> None:
    body = b'{"action":"opened"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d1",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 401


def test_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _test_settings()
    cfg = Settings(
        host=base.host,
        port=base.port,
        github_webhook_secret=base.github_webhook_secret,
        opencode_server_url=base.opencode_server_url,
        prompt_script=base.prompt_script,
        workspace=base.workspace,
        model=base.model,
        agent=base.agent,
        max_payload_chars=base.max_payload_chars,
        max_body_bytes=8,
        log_level=base.log_level,
        enable_simulator=base.enable_simulator,
        beads_enabled=base.beads_enabled,
        beads_poll_interval=base.beads_poll_interval,
        beads_max_retries=base.beads_max_retries,
        beads_workspace_root=base.beads_workspace_root,
    )
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    client = TestClient(create_app(cfg))
    body = b"x" * 9
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "d-big",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
        },
    )
    assert response.status_code == 413
    dispatch.assert_not_called()


def test_accepts_issue_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "test-user"},
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "delivery-issues",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    dispatch.assert_called_once()


def test_filters_issue_comment_to_prevent_echo_loop(
    client: TestClient,
) -> None:
    """Regression for the gap-miner-v2 cascade.

    ``issue_comment.created`` must never dispatch the agent (the prompt has no
    clause for it, so ``(default)`` would post a comment and re-trigger).
    """
    payload = {
        "action": "created",
        "comment": {"body": "ping"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "someone"},
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-GitHub-Delivery": "d-echo",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"


def test_filters_issues_opened_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``issues.opened`` is not a dispatch trigger (only ``labeled`` is)."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    payload = {
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "someone"},
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d-opened",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    dispatch.assert_not_called()


def test_filters_bot_actors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``labeled`` event applied by an App/bot must not dispatch."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "github-actions[bot]"},
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d-bot",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    dispatch.assert_not_called()


def test_filters_non_workflow_label(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``labeled`` event with a non-workflow label must not dispatch."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    payload = {
        "action": "labeled",
        "label": {"name": "bug"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "someone"},
    }
    body = json.dumps(payload).encode()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d-label",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    dispatch.assert_not_called()


# ── _safe_dispatch: workspace bootstrap & root guard ──────────────────────


def _post_issues(
    client: TestClient, payload: dict, delivery: str = "d1"
):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )


def test_safe_dispatch_inits_project_when_no_clone_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook with no valid clone_url → init_project_workspace called + dispatch runs."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    init_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.init_project_workspace", init_project)
    ensure_clone = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.ensure_project_from_clone", ensure_clone)

    client = TestClient(create_app(_test_settings()))
    # No clone_url (and none is a valid HTTPS URL) → bootstrap path.
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "test-user"},
    }

    response = _post_issues(client, payload, delivery="d-init")

    assert response.status_code == 202
    # The subdir must be git-init'd (base, slug).
    init_project.assert_called_once_with("/workspace", "org-repo")
    # Clone path must NOT be taken.
    ensure_clone.assert_not_called()
    # Dispatch still happens against the bootstrapped workspace.
    dispatch.assert_called_once()


def test_safe_dispatch_clones_when_valid_clone_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook with a valid HTTPS clone_url → ensure_project_from_clone + sync, no init."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    init_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.init_project_workspace", init_project)
    ensure_clone = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.ensure_project_from_clone", ensure_clone)
    sync_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.sync_project", sync_project)

    client = TestClient(create_app(_test_settings()))
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {
            "full_name": "org/repo",
            "clone_url": "https://github.com/org/repo.git",
        },
        "sender": {"login": "test-user"},
    }

    response = _post_issues(client, payload, delivery="d-clone")

    assert response.status_code == 202
    ensure_clone.assert_called_once_with(
        "/workspace", "org-repo", "https://github.com/org/repo.git", base_branch="main"
    )
    sync_project.assert_called_once_with("/workspace/org-repo", branch="main")
    init_project.assert_not_called()
    dispatch.assert_called_once()


def test_safe_dispatch_threads_payload_default_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook with default_branch=master → clone/sync use master, not main."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    init_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.init_project_workspace", init_project)
    ensure_clone = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.ensure_project_from_clone", ensure_clone)
    sync_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.sync_project", sync_project)

    client = TestClient(create_app(_test_settings()))
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {
            "full_name": "org/repo",
            "clone_url": "https://github.com/org/repo.git",
            "default_branch": "master",
        },
        "sender": {"login": "test-user"},
    }

    response = _post_issues(client, payload, delivery="d-branch")

    assert response.status_code == 202
    ensure_clone.assert_called_once_with(
        "/workspace", "org-repo", "https://github.com/org/repo.git", base_branch="master"
    )
    sync_project.assert_called_once_with("/workspace/org-repo", branch="master")
    init_project.assert_not_called()
    dispatch.assert_called_once()


def test_safe_dispatch_root_guard_refuses_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the derived slug resolves to the workspace root, dispatch is refused.

    The guard is defensive: ``_derive_project_slug`` always yields a non-empty
    slug, so we force the empty-slug case by monkeypatching it and assert the
    background task refuses to dispatch without raising.
    """
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    init_project = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.init_project_workspace", init_project)
    # Force slug="" so project_workspace_path(base, "") == base → root guard fires.
    monkeypatch.setattr(
        "webhook_receiver.app._derive_project_slug", lambda payload: ""
    )

    client = TestClient(create_app(_test_settings()))
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "test-user"},
    }

    response = _post_issues(client, payload, delivery="d-guard")

    # The HTTP handler still accepts (202) — the guard is in the background task.
    assert response.status_code == 202
    # But the background task refused to dispatch or init at the root.
    dispatch.assert_not_called()
    init_project.assert_not_called()


def test_safe_dispatch_normal_path_does_not_hit_root_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: a normal non-empty slug resolves below base, so dispatch proceeds."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    monkeypatch.setattr("webhook_receiver.app.init_project_workspace", MagicMock())

    client = TestClient(create_app(_test_settings()))
    payload = {
        "action": "labeled",
        "label": {"name": "orchestration:dispatch"},
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "test-user"},
    }

    response = _post_issues(client, payload, delivery="d-normal")

    assert response.status_code == 202
    dispatch.assert_called_once()


# ── _safe_branch: default-branch sanitization ──────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("main", "main"),
        ("master", "master"),
        ("develop", "develop"),
        ("release/1.0", "release/1.0"),
        ("", "main"),
        ("   ", "main"),
        (None, "main"),
        (123, "main"),
        ("-x", "main"),  # leading dash would be a git flag
        ("--upload-pack=evil", "main"),
        ("a..b", "main"),  # path traversal
        ("..", "main"),
        (".", "main"),
        ("weird branch", "main"),  # space not in allowlist
    ],
)
def test_safe_branch(value: object, expected: str) -> None:
    from webhook_receiver.app import _safe_branch

    assert _safe_branch(value) == expected
