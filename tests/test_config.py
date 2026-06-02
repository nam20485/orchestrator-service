import pytest

from webhook_receiver.config import Settings


def test_settings_from_env_requires_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OS_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError, match="OS_WEBHOOK_SECRET"):
        Settings.from_env()


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OS_WEBHOOK_SECRET", "test-secret")
    monkeypatch.delenv("WEBHOOK_ALLOWED_EVENTS", raising=False)
    cfg = Settings.from_env()
    assert cfg.github_webhook_secret == "test-secret"
    assert cfg.opencode_server_url == "http://localhost:4099"
    assert cfg.agent == "orchestrator"
    assert cfg.allowed_events is None


def test_settings_parses_allowed_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OS_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("WEBHOOK_ALLOWED_EVENTS", "issues, pull_request")
    cfg = Settings.from_env()
    assert cfg.allowed_events == frozenset({"issues", "pull_request"})
