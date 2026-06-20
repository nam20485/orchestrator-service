from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_prompt_script() -> Path:
    return _repo_root() / "scripts" / "prompt.ps1"


# GitHub webhook payloads are capped at 25 MB.
_DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024


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
    allowed_events: frozenset[str] | None
    max_payload_chars: int
    max_body_bytes: int
    log_level: str
    enable_simulator: bool
    beads_enabled: bool
    beads_poll_interval: int
    beads_max_retries: int
    beads_workspace_root: str
    beads_target_repo: str

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("OS_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise ValueError(
                "OS_WEBHOOK_SECRET is required (GitHub App webhook secret)."
            )

        allowed_raw = os.environ.get("WEBHOOK_ALLOWED_EVENTS", "").strip()
        allowed: frozenset[str] | None = None
        if allowed_raw:
            allowed = frozenset(
                e.strip().lower() for e in allowed_raw.split(",") if e.strip()
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
            model=os.environ.get("OPENCODE_MODEL", "zai-coding-plan/glm-4.7"),
            agent=os.environ.get("OPENCODE_AGENT", "orchestrator"),
            allowed_events=allowed,
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
            beads_target_repo=os.environ.get("BEADS_TARGET_REPO", "").strip(),
        )
