from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webhook_receiver.app import create_app
from webhook_receiver.config import Settings
from webhook_receiver.simulator_templates import get_template, merge_template


def _test_settings(*, enable_simulator: bool = True) -> Settings:
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
        enable_simulator=enable_simulator,
    )


def test_simulator_disabled_returns_404() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=False)))
    assert client.get("/simulator").status_code == 404
    assert client.get("/simulator/api/templates").status_code == 404


def test_simulator_page_returns_html() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=True)))
    response = client.get("/simulator")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "GitHub Webhook Simulator" in response.text


def test_simulator_template_list() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=True)))
    safe = client.get("/simulator/api/templates?safe_only=true")
    assert safe.status_code == 200
    assert safe.json()["events"] == ["ping"]

    work = client.get("/simulator/api/templates?safe_only=false")
    assert work.status_code == 200
    assert "issues" in work.json()["events"]


def test_simulator_template_ping() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=True)))
    response = client.get("/simulator/api/templates/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["event"] == "ping"
    assert "zen" in data["payload"]
    assert "hook_id" in data["payload"]


def test_simulator_unknown_event_404() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=True)))
    assert client.get("/simulator/api/templates/not_real").status_code == 404


def test_get_template_issues_with_overrides() -> None:
    payload = get_template(
        "issues", repo="acme/widgets", action="labeled", number=99
    )
    assert payload["action"] == "labeled"
    assert payload["repository"]["full_name"] == "acme/widgets"
    assert payload["issue"]["number"] == 99


def test_merge_template_applies_fields() -> None:
    base = get_template("issues")
    merged = merge_template(base, repo="x/y", action="closed", number=7)
    assert merged["repository"]["full_name"] == "x/y"
    assert merged["action"] == "closed"
    assert merged["issue"]["number"] == 7


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("0", False),
        ("", False),
    ],
)
def test_enable_simulator_env(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("OS_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("WEBHOOK_ENABLE_SIMULATOR", raw)
    cfg = Settings.from_env()
    assert cfg.enable_simulator is expected
