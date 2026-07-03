from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webhook_receiver.app import create_app
from webhook_receiver.config import Settings
from webhook_receiver.simulator_templates import get_template, merge_template

# Shared fake token used to authenticate the (now gated) simulator surface.
_TOKEN = "test-dashboard-token"


def _test_settings(
    *, enable_simulator: bool = True, dashboard_token: str | None = _TOKEN
) -> Settings:
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
        enable_simulator=enable_simulator,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        dashboard_token=dashboard_token,
    )


def _client(
    *,
    enable_simulator: bool = True,
    token: str | None = _TOKEN,
    settings: Settings | None = None,
) -> TestClient:
    """A TestClient authenticated against the gated simulator surface.

    Sets the ``dashboard_token`` cookie so every same-origin request to
    ``/simulator/*`` passes the auth gate, mirroring how the browser persists
    the token after opening ``/simulator?token=...``.
    """
    client = TestClient(create_app(settings or _test_settings(enable_simulator=enable_simulator)))
    if token:
        client.cookies.set("dashboard_token", token)
    return client


def test_simulator_disabled_returns_404() -> None:
    client = TestClient(create_app(_test_settings(enable_simulator=False)))
    assert client.get("/simulator").status_code == 404
    assert client.get("/simulator/api/templates").status_code == 404


def test_simulator_page_returns_html() -> None:
    client = _client()
    response = client.get("/simulator")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert response.headers.get("cache-control") == "no-store"
    assert "GitHub Webhook Simulator" in response.text
    # The webhook secret must never be embedded in the served HTML.
    assert "test-webhook-secret" not in response.text
    assert "ENV_WEBHOOK_SECRET" not in response.text


def test_simulator_template_list() -> None:
    client = _client()
    safe = client.get("/simulator/api/templates?safe_only=true")
    assert safe.status_code == 200
    assert safe.json()["events"] == ["ping"]

    work = client.get("/simulator/api/templates?safe_only=false")
    assert work.status_code == 200
    assert "issues" in work.json()["events"]


def test_simulator_template_ping() -> None:
    client = _client()
    response = client.get("/simulator/api/templates/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["event"] == "ping"
    assert "zen" in data["payload"]
    assert "hook_id" in data["payload"]


def test_simulator_unknown_event_404() -> None:
    client = _client()
    assert client.get("/simulator/api/templates/not_real").status_code == 404


def test_get_template_issues_with_overrides() -> None:
    payload = get_template(
        "issues", repo="acme/widgets", action="labeled", number=99
    )
    assert payload["action"] == "labeled"
    assert payload["repository"]["full_name"] == "acme/widgets"
    assert payload["issue"]["number"] == 99
    # A labeled template must carry a workflow-relevant label so it dispatches.
    assert payload["label"]["name"] == "orchestration:dispatch"


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


def test_simulator_page_html_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    from webhook_receiver import simulator as sim_mod

    orig = sim_mod._STATIC_DIR
    sim_mod._STATIC_DIR = Path("/nonexistent-static-dir")
    try:
        response = client.get("/simulator")
        assert response.status_code == 500
    finally:
        sim_mod._STATIC_DIR = orig


def test_simulator_template_bad_action(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    response = client.get("/simulator/api/templates/custom")
    assert response.status_code == 200
    data = response.json()
    assert data["payload"]["action"] == "opened"


def test_simulator_send_signs_server_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """The send endpoint computes the HMAC server-side and never echoes the secret."""
    import json as _json

    from webhook_receiver import simulator as sim_mod
    from webhook_receiver.github import compute_signature

    monkeypatch.setenv("OS_WEBHOOK_SECRET", "test-webhook-secret")

    captured: dict[str, object] = {}

    class _FakeResp:
        status_code = 200
        text = '{"status": "pong"}'

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["body"] = content
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(sim_mod.httpx, "AsyncClient", _FakeClient)

    client = _client()
    payload = {"zen": "hello", "hook_id": 42}
    response = client.post(
        "/simulator/api/send",
        json={"event": "ping", "deliveryId": "abc-123", "payload": payload},
    )

    assert response.status_code == 200
    assert response.json() == {"status": 200, "body": '{"status": "pong"}'}

    # Forwarded over loopback to the receiver route on this same process
    # (never the external Host the request came in on).
    assert captured["url"] == "http://127.0.0.1:8080/webhooks/github"
    headers = captured["headers"]
    assert headers["X-GitHub-Event"] == "ping"
    assert headers["X-GitHub-Delivery"] == "abc-123"
    # Signature matches what the receiver expects for this body+secret.
    expected_sig = compute_signature(captured["body"], "test-webhook-secret")
    assert headers["X-Hub-Signature-256"] == expected_sig
    # The secret is never present in the forwarded request.
    assert "test-webhook-secret" not in _json.dumps(dict(headers))


def test_simulator_send_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OS_WEBHOOK_SECRET", raising=False)
    client = _client()
    response = client.post(
        "/simulator/api/send",
        json={"event": "ping", "deliveryId": "x", "payload": {"a": 1}},
    )
    assert response.status_code == 500
    assert "OS_WEBHOOK_SECRET" in response.json()["detail"]


# ── simulator auth gate (Comment 1 security fix) ──────────────────────────


def test_simulator_send_requires_token_when_set() -> None:
    """No token at all -> 401 on every simulator route (fail-closed)."""
    client = _client(token=None)
    response = client.post(
        "/simulator/api/send",
        json={"event": "ping", "deliveryId": "x", "payload": {"a": 1}},
    )
    assert response.status_code == 401


def test_simulator_send_allows_with_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OS_WEBHOOK_SECRET", raising=False)
    client = TestClient(create_app(_test_settings()))
    response = client.post(
        "/simulator/api/send",
        json={"event": "ping", "deliveryId": "x", "payload": {"zen": "hi"}},
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )
    # Auth passed (not 401); the request reached the secret check (500 because
    # OS_WEBHOOK_SECRET is unset), proving the bearer token was accepted.
    assert response.status_code == 500
    assert "OS_WEBHOOK_SECRET" in response.json()["detail"]


def test_simulator_send_allows_with_query_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OS_WEBHOOK_SECRET", raising=False)
    client = TestClient(create_app(_test_settings()))
    response = client.post(
        "/simulator/api/send?token=" + _TOKEN,
        json={"event": "ping", "deliveryId": "x", "payload": {"zen": "hi"}},
    )
    assert response.status_code == 500
    assert "OS_WEBHOOK_SECRET" in response.json()["detail"]


def test_simulator_send_rejects_wrong_token() -> None:
    client = TestClient(create_app(_test_settings()))
    response = client.post(
        "/simulator/api/send",
        json={"event": "ping", "deliveryId": "x", "payload": {"a": 1}},
        headers={"Authorization": "Bearer not-the-real-token"},
    )
    assert response.status_code == 401


def test_simulator_enabled_without_dashboard_token_returns_401() -> None:
    """Enabled simulator with no DASHBOARD_TOKEN configured is fail-closed."""
    client = _client(settings=_test_settings(enable_simulator=True, dashboard_token=None))
    assert client.get("/simulator").status_code == 401
    assert client.get("/simulator/api/templates").status_code == 401
    resp = client.post(
        "/simulator/api/send", json={"event": "ping", "payload": {}}
    )
    assert resp.status_code == 401
    assert "DASHBOARD_TOKEN" in resp.json()["detail"]


def test_simulator_page_sets_cookie_on_token_query() -> None:
    client = TestClient(create_app(_test_settings()))
    response = client.get("/simulator", params={"token": _TOKEN}, follow_redirects=False)
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "dashboard_token=" + _TOKEN in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()


def test_simulator_page_rejects_invalid_token_query() -> None:
    client = TestClient(create_app(_test_settings()))
    response = client.get("/simulator", params={"token": "nope"}, follow_redirects=False)
    assert response.status_code == 401

