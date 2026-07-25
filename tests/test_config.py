import pytest

from webhook_receiver.config import Settings


def test_settings_from_env_requires_webhook_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OS_WEBHOOK_SECRET", raising=False)
    with pytest.raises(ValueError, match="OS_WEBHOOK_SECRET"):
        Settings.from_env()


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear every input from_env() reads so the defaults are deterministic
    # regardless of the developer shell or CI environment.
    for name in (
        "WEBHOOK_HOST",
        "WEBHOOK_PORT",
        "OPENCODE_SERVER_URL",
        "PROMPT_SCRIPT",
        "ORCHESTRATOR_WORKSPACE",
        "OPENCODE_MODEL",
        "OPENCODE_AGENT",
        "WEBHOOK_MAX_PAYLOAD_CHARS",
        "WEBHOOK_MAX_BODY_BYTES",
        "WEBHOOK_LOG_LEVEL",
        "WEBHOOK_ENABLE_SIMULATOR",
        "BEADS_ENABLED",
        "BEADS_POLL_INTERVAL",
        "BEADS_MAX_RETRIES",
        "BEADS_WORKSPACE_ROOT",
        "DASHBOARD_TOKEN",
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_TRACES_SAMPLE_RATE",
        "SENTRY_RELEASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OS_WEBHOOK_SECRET", "test-secret")
    cfg = Settings.from_env()
    assert cfg.github_webhook_secret == "test-secret"
    assert cfg.opencode_server_url == "http://localhost:4099"
    assert cfg.agent == "orchestrator"
    assert cfg.max_body_bytes == 25 * 1024 * 1024
    assert cfg.beads_enabled is True
    assert cfg.beads_poll_interval == 10
    assert cfg.beads_max_retries == 3
    assert cfg.beads_workspace_root == "/workspace"
    assert cfg.dashboard_token is None
    # log_dir defaults to the in-container path the compose bind mount covers.
    assert cfg.log_dir.name == "orchestrator-webhook"
    # Sentry is inert by default (no DSN configured).
    assert cfg.sentry_dsn is None
    assert cfg.sentry_environment == "production"
    assert cfg.sentry_traces_sample_rate == 0.0
    assert cfg.sentry_release is None


def test_settings_from_env_reads_sentry_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OS_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("SENTRY_DSN", "https://FAKE-SENTRY-DSN-FOR-TESTING@o0.ingest.sentry.io/0")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    monkeypatch.setenv("SENTRY_RELEASE", "v1.2.3")
    cfg = Settings.from_env()
    assert cfg.sentry_dsn == "https://FAKE-SENTRY-DSN-FOR-TESTING@o0.ingest.sentry.io/0"
    assert cfg.sentry_environment == "staging"
    assert cfg.sentry_traces_sample_rate == 0.25
    assert cfg.sentry_release == "v1.2.3"
