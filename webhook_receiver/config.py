from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_prompt_script() -> Path:
    return _repo_root() / "scripts" / "prompt.ps1"


# GitHub webhook payloads are capped at 25 MB.
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024


def default_log_dir() -> Path:
    """In-container directory for per-run logs + the bvr pages bundle.

    The compose bind mount maps the host ``WEBHOOK_LOG_DIR`` onto this exact
    container path (see ``compose.yaml``, ``webhook-receiver.volumes``) so the
    captured stdout/stderr/manifests persist on the host and survive container
    restarts. Code MUST read this path from :attr:`Settings.log_dir` (which
    defaults to this) rather than re-deriving ``Path(tempfile.gettempdir()) /
    "orchestrator-webhook"`` in three places.
    """
    return Path(tempfile.gettempdir()) / "orchestrator-webhook"


def _parse_optional_int(name: str) -> int | None:
    """Read an optional positive-int env var; empty/missing/unset -> None."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return int(raw)


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    github_webhook_secret: str
    opencode_server_url: str
    prompt_script: Path
    workspace: str
    model: str
    agent: str
    max_payload_chars: int
    max_body_bytes: int
    log_level: str
    enable_simulator: bool
    beads_enabled: bool
    beads_poll_interval: int
    beads_max_retries: int
    beads_workspace_root: str
    # Shared secret required to access the dashboard UI and APIs. When unset
    # (default) the entire dashboard surface is disabled and returns 404, so
    # the receiver cannot leak beads data through the proxy by default.
    dashboard_token: str | None = None
    # Optional hard wall-clock timeout (seconds) for a dispatched opencode run.
    # When set (env DISPATCH_TIMEOUT_SECS), a run exceeding it is killed and a
    # failure comment is posted. None (default) = no timeout (wait forever).
    # Recommended value ~5400 (≈ the golden-path project-setup runtime) so a
    # hung run is killed and flagged instead of waiting forever.
    dispatch_timeout: int | None = None
    # Directory runner.py / beads_loop.py / dashboard.py use for per-run logs,
    # run manifests, and the bvr pages bundle. Defaults to the in-container path
    # covered by the compose bind mount; tests override it with a tmp dir.
    log_dir: Path = field(default_factory=default_log_dir)

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("OS_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise ValueError(
                "OS_WEBHOOK_SECRET is required (GitHub App webhook secret)."
            )

        return cls(
            host=os.environ.get("WEBHOOK_HOST", "0.0.0.0"),
            port=int(os.environ.get("WEBHOOK_PORT", "8080")),
            github_webhook_secret=secret,
            opencode_server_url=os.environ.get(
                "OPENCODE_SERVER_URL", "http://localhost:4099"
            ).rstrip("/"),
            prompt_script=Path(
                os.environ.get("PROMPT_SCRIPT", str(_default_prompt_script()))
            ).resolve(),
            workspace=os.environ.get("ORCHESTRATOR_WORKSPACE", "/workspace"),
            model=os.environ.get("OPENCODE_MODEL", "zai-coding-plan/glm-5"),
            agent=os.environ.get("OPENCODE_AGENT", "orchestrator"),
            max_payload_chars=int(
                os.environ.get("WEBHOOK_MAX_PAYLOAD_CHARS", "120000")
            ),
            max_body_bytes=int(
                os.environ.get("WEBHOOK_MAX_BODY_BYTES", str(_DEFAULT_MAX_BODY_BYTES))
            ),
            log_level=os.environ.get("WEBHOOK_LOG_LEVEL", "info").lower(),
            enable_simulator=os.environ.get("WEBHOOK_ENABLE_SIMULATOR", "")
            .strip()
            .lower()
            in ("1", "true", "yes"),
            beads_enabled=os.environ.get("BEADS_ENABLED", "true")
            .strip()
            .lower()
            in ("1", "true", "yes"),
            beads_poll_interval=int(os.environ.get("BEADS_POLL_INTERVAL", "10")),
            beads_max_retries=int(os.environ.get("BEADS_MAX_RETRIES", "3")),
            beads_workspace_root=os.environ.get("BEADS_WORKSPACE_ROOT", "/workspace"),
            dashboard_token=(os.environ.get("DASHBOARD_TOKEN", "").strip() or None),
            dispatch_timeout=_parse_optional_int("DISPATCH_TIMEOUT_SECS"),
        )
