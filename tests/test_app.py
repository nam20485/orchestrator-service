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
        log_level="warning",
        enable_simulator=False,
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
        log_level=base.log_level,
        enable_simulator=base.enable_simulator,
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
