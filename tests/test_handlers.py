from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks

from webhook_receiver.config import Settings
from webhook_receiver.handlers.base import WebhookContext
from webhook_receiver.handlers.ignored import IgnoredEventHandler
from webhook_receiver.handlers.orchestration import OrchestrationHandler
from webhook_receiver.handlers.ping import PingHandler
from webhook_receiver.handlers.registry import HandlerRegistry, build_handler_registry


def _test_settings(*, allowed_events: frozenset[str] | None = None) -> Settings:
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
        allowed_events=allowed_events,
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
    )


def _ctx(
    *,
    event: str = "issues",
    payload: dict | None = None,
    settings: Settings | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> WebhookContext:
    return WebhookContext(
        delivery_id="delivery-1",
        event=event,
        payload=payload or {"action": "opened"},
        settings=settings or _test_settings(),
        background_tasks=background_tasks or BackgroundTasks(),
    )


def test_ping_handler_matches_and_returns_pong() -> None:
    handler = PingHandler()
    ctx = _ctx(event="ping", payload={"zen": "test"})

    assert handler.matches(ctx)
    response = handler.handle(ctx)

    assert response.status_code == 200
    assert response.body == b'{"status":"pong","delivery_id":"delivery-1"}'


def test_ping_handler_does_not_match_issues() -> None:
    assert not PingHandler().matches(_ctx(event="issues"))


def test_ignored_handler_matches_disallowed_event() -> None:
    settings = _test_settings(allowed_events=frozenset({"pull_request"}))
    handler = IgnoredEventHandler(settings)
    ctx = _ctx(event="issues", settings=settings)

    assert handler.matches(ctx)
    response = handler.handle(ctx)

    assert response.status_code == 202
    body = response.body.decode()
    assert '"status":"ignored"' in body.replace(" ", "")
    assert "WEBHOOK_ALLOWED_EVENTS" in body


def test_ignored_handler_does_not_match_when_allowlist_open() -> None:
    handler = IgnoredEventHandler(_test_settings(allowed_events=None))
    assert not handler.matches(_ctx(event="issues"))


def test_ignored_handler_does_not_match_allowed_event() -> None:
    settings = _test_settings(allowed_events=frozenset({"issues"}))
    handler = IgnoredEventHandler(settings)
    assert not handler.matches(_ctx(event="issues", settings=settings))


def test_orchestration_handler_always_matches() -> None:
    handler = OrchestrationHandler(_test_settings())
    assert handler.matches(_ctx(event="issues"))
    assert handler.matches(_ctx(event="pull_request"))


def test_orchestration_handler_dispatches_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = MagicMock()
    monkeypatch.setattr(
        "webhook_receiver.handlers.orchestration.dispatch_to_opencode", dispatch
    )
    background_tasks = BackgroundTasks()
    settings = _test_settings()
    handler = OrchestrationHandler(settings)
    ctx = _ctx(
        event="issues",
        payload={"action": "opened", "repository": {"full_name": "org/repo"}},
        settings=settings,
        background_tasks=background_tasks,
    )

    response = handler.handle(ctx)
    task = background_tasks.tasks[0]
    task.func(*task.args, **task.kwargs)

    assert response.status_code == 202
    assert '"status":"accepted"' in response.body.decode().replace(" ", "")
    dispatch.assert_called_once()


def test_registry_dispatches_ping_before_orchestration() -> None:
    settings = _test_settings()
    registry = build_handler_registry(settings)
    ctx = _ctx(event="ping", payload={"zen": "test"}, settings=settings)

    response = registry.dispatch(ctx)

    assert response.status_code == 200


def test_registry_dispatches_ignored_before_orchestration() -> None:
    settings = _test_settings(allowed_events=frozenset({"pull_request"}))
    registry = build_handler_registry(settings)
    ctx = _ctx(event="issues", settings=settings)

    response = registry.dispatch(ctx)

    assert response.status_code == 202
    assert b'"status":"ignored"' in response.body


def test_registry_raises_when_no_handler_matches() -> None:
    registry = HandlerRegistry([])

    with pytest.raises(RuntimeError, match="No handler"):
        registry.dispatch(_ctx())
