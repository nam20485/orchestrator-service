from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver import observability
from webhook_receiver.config import Settings


def _test_settings(**overrides: object) -> Settings:
    repo = Path(__file__).resolve().parent.parent
    defaults = dict(
        host="127.0.0.1",
        port=8080,
        github_webhook_secret="test-secret",
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
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _reset_sentry_state() -> None:
    """Every test starts and ends with Sentry uninitialized (module-global flag)."""
    observability._active = False
    yield
    observability._active = False


def test_init_sentry_noop_without_dsn() -> None:
    settings = _test_settings(sentry_dsn=None)
    assert observability.init_sentry(settings) is False
    assert observability._active is False


def test_capture_dispatch_failure_noop_when_not_initialized() -> None:
    with patch("sentry_sdk.capture_message") as mock_capture:
        observability.capture_dispatch_failure("should not be sent", foo="bar")
    mock_capture.assert_not_called()


def test_init_sentry_configures_sdk_when_dsn_set() -> None:
    settings = _test_settings(
        sentry_dsn="https://FAKE-SENTRY-DSN-FOR-TESTING@o0.ingest.sentry.io/0",
        sentry_environment="staging",
        sentry_release="v1.2.3",
        sentry_traces_sample_rate=0.5,
    )
    with patch("sentry_sdk.init") as mock_init:
        assert observability.init_sentry(settings) is True
    mock_init.assert_called_once_with(
        dsn="https://FAKE-SENTRY-DSN-FOR-TESTING@o0.ingest.sentry.io/0",
        environment="staging",
        release="v1.2.3",
        traces_sample_rate=0.5,
        send_default_pii=False,
    )
    assert observability._active is True


def test_capture_dispatch_failure_sends_when_active() -> None:
    settings = _test_settings(
        sentry_dsn="https://FAKE-SENTRY-DSN-FOR-TESTING@o0.ingest.sentry.io/0"
    )
    with patch("sentry_sdk.init"):
        observability.init_sentry(settings)

    mock_scope = MagicMock()
    mock_scope.__enter__.return_value = mock_scope
    mock_scope.__exit__.return_value = False

    with (
        patch("sentry_sdk.new_scope", return_value=mock_scope) as mock_new_scope,
        patch("sentry_sdk.capture_message") as mock_capture,
    ):
        observability.capture_dispatch_failure(
            "dispatch failed", exit_code="1", kill_reason=None, repo="owner/repo"
        )

    mock_new_scope.assert_called_once()
    mock_scope.set_tag.assert_any_call("exit_code", "1")
    mock_scope.set_tag.assert_any_call("repo", "owner/repo")
    assert ("kill_reason",) not in [call.args for call in mock_scope.set_tag.call_args_list]
    mock_capture.assert_called_once_with("dispatch failed", level="error")


def test_init_sentry_reset_by_fixture_between_tests() -> None:
    # Sanity check that the autouse fixture actually resets module state --
    # if this test ran after test_init_sentry_configures_sdk_when_dsn_set
    # without the reset, _active would still be True here.
    assert observability._active is False
