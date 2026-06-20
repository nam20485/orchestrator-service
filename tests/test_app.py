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
        allowed_events=None,
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        beads_target_repo="",
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
        allowed_events=base.allowed_events,
        max_payload_chars=base.max_payload_chars,
        max_body_bytes=8,
        log_level=base.log_level,
        enable_simulator=base.enable_simulator,
        beads_enabled=base.beads_enabled,
        beads_poll_interval=base.beads_poll_interval,
        beads_max_retries=base.beads_max_retries,
        beads_workspace_root=base.beads_workspace_root,
        beads_target_repo=base.beads_target_repo,
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
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "bot"},
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


def test_ignores_disallowed_event(monkeypatch: pytest.MonkeyPatch) -> None:
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
        allowed_events=frozenset({"pull_request"}),
        max_payload_chars=base.max_payload_chars,
        max_body_bytes=base.max_body_bytes,
        log_level=base.log_level,
        enable_simulator=base.enable_simulator,
        beads_enabled=base.beads_enabled,
        beads_poll_interval=base.beads_poll_interval,
        beads_max_retries=base.beads_max_retries,
        beads_workspace_root=base.beads_workspace_root,
        beads_target_repo=base.beads_target_repo,
    )
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    client = TestClient(create_app(cfg))
    body = b'{"action":"opened"}'
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d2",
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    dispatch.assert_not_called()
