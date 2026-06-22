"""Stage 1 integration: webhook HTTP → prompt assembly → dispatch.

Tests the full HTTP request lifecycle with real prompt assembly (jinja2 template)
and mocked dispatch. Exercises signature verification, event filtering, payload
truncation, and the inter-stage boundary between webhook receipt and dispatch.
"""
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


def _test_settings(**overrides: object) -> Settings:
    repo = Path(__file__).resolve().parent.parent
    defaults = dict(
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
    defaults.update(overrides)
    return Settings(**defaults)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)
    return TestClient(create_app(_test_settings()))


def _post(client: TestClient, event: str, payload: dict, delivery: str = "d1") -> object:
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sign(body, "test-webhook-secret"),
            "Content-Type": "application/json",
        },
    )


# ── Stage 1: webhook → prompt assembly ────────────────────────────────────


def test_webhook_to_prompt_assembly_issues_event(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST issues event → dispatch called with a prompt containing event JSON."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)

    payload = {
        "action": "opened",
        "repository": {"full_name": "org/repo"},
        "sender": {"login": "bot"},
        "issue": {"number": 42, "title": "Bug fix"},
    }
    resp = _post(client, "issues", payload)

    assert resp.status_code == 202
    dispatch.assert_called_once()
    prompt = dispatch.call_args[0][1]
    assert "org/repo" in prompt
    assert "opened" in prompt
    assert "Bug fix" in prompt


def test_webhook_to_prompt_truncates_large_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payload exceeding max_payload_chars → prompt has truncation notice."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)

    cfg = _test_settings(max_payload_chars=100)
    client = TestClient(create_app(cfg))

    big_payload = {
        "action": "opened",
        "repository": {"full_name": "o/r"},
        "sender": {"login": "b"},
        "data": "X" * 5000,
    }
    _post(client, "issues", big_payload)

    dispatch.assert_called_once()
    prompt = dispatch.call_args[0][1]
    assert "truncated" in prompt.lower()


def test_webhook_to_prompt_ping_skips_dispatch(client: TestClient) -> None:
    """Ping event → 200 pong, dispatch NOT called."""
    resp = _post(client, "ping", {"zen": "test"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "pong"


def test_webhook_to_prompt_bad_signature_rejects(client: TestClient) -> None:
    """Wrong signature → 401, dispatch NOT called."""
    body = json.dumps({"action": "opened"}).encode()
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "d2",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert resp.status_code == 401


def test_webhook_to_prompt_allowed_events_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """issues event not in allowed_events={pull_request} → 202 ignored."""
    dispatch = MagicMock()
    monkeypatch.setattr("webhook_receiver.app.dispatch_to_opencode", dispatch)

    cfg = _test_settings(allowed_events=frozenset({"pull_request"}))
    client = TestClient(create_app(cfg))

    _post(client, "issues", {"action": "opened"})

    dispatch.assert_not_called()
