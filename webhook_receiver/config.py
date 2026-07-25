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
    # Reasoning-effort variant passed to opencode via --variant (e.g. "high",
    # "medium", "minimal"). Sets the orchestrator session default; subagents may
    # override per-agent via the opencode.json "agent" block. Empty string
    # omits --variant entirely (uses the provider default). GLM-5 supports
    # low/medium/high (high is the ceiling — there is no "max").
    variant: str = "high"
    # Shared secret required to access the dashboard UI and APIs. When unset
    # (default) the entire dashboard surface is disabled and returns 404, so
    # the receiver cannot leak beads data through the proxy by default.
    dashboard_token: str | None = None
    # ── Idle watchdog configuration ─────────────────────────────────────────
    # The watchdog replaces the previous single wall-clock timeout
    # (DISPATCH_TIMEOUT_SECS) with activity-aware monitoring. A run is killed
    # if (a) it produces no stdout/stderr output for IDLE_TIMEOUT_SECS, (b) it
    # emits MAX_CONSECUTIVE_ERRORS error lines without a non-error line, or
    # (c) it exceeds HARD_CEILING_SECS regardless of activity. See watchdog.py
    # and docs/idle-timeout-implementation-report.md for the full design
    # rationale (ported from the battle-tested bash watchdog in
    # intel-agency/workflow-orchestration-service).
    #
    # HARD_CEILING_SECS is the absolute safety net. It accepts the legacy
    # DISPATCH_TIMEOUT_SECS env var for backward compatibility.
    dispatch_timeout: int | None = None
    idle_timeout_secs: int = 900
    error_grace_secs: int = 300
    hard_ceiling_secs: int | None = 5400
    watchdog_poll_secs: int = 30
    max_consecutive_errors: int = 5
    watchdog_debug: bool = False
    # Grace window before an unanswered permission `ask` (detected in the
    # opencode server log) is treated as a fatal headless deadlock and the run
    # is killed. In headless dispatches no `ask` can be answered, so this is
    # short; it only absorbs a transiently-logged ask that opencode resolves
    # via a saved "always" approval (impossible for skip-perms subagents).
    permission_ask_grace_secs: int = 60
    # Path to the opencode server's log file, shared via the opencode-logs
    # volume (see compose.yaml). The watchdog treats it as a per-dispatch
    # activity signal by tracking byte growth since the run started: if the
    # client stdout is silent (blocked on a subagent delegation) but the server
    # log keeps growing, the agent is working and the kill is withheld. A
    # non-growing or pre-existing log does not reset the idle clock, so it
    # cannot mask a stuck run the way a global mtime check would. Empty string
    # disables the signal (falls back to client-only monitoring).
    server_log_path: str = "/home/app/.local/share/opencode/log/opencode.log"
    # Directory runner.py / beads_loop.py / dashboard.py use for per-run logs,
    # run manifests, and the bvr pages bundle. Defaults to the in-container path
    # covered by the compose bind mount; tests override it with a tmp dir.
    log_dir: Path = field(default_factory=default_log_dir)
    # ── Error tracking (Sentry) ──────────────────────────────────────────────
    # Sentry is inert until sentry_dsn is set; see observability.py. Never
    # required, never printed/logged (it is a bearer-style ingest URL).
    sentry_dsn: str | None = None
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.0
    sentry_release: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("OS_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise ValueError("OS_WEBHOOK_SECRET is required (GitHub App webhook secret).")

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
            variant=os.environ.get("OPENCODE_VARIANT", "high"),
            agent=os.environ.get("OPENCODE_AGENT", "orchestrator"),
            max_payload_chars=int(os.environ.get("WEBHOOK_MAX_PAYLOAD_CHARS", "120000")),
            max_body_bytes=int(
                os.environ.get("WEBHOOK_MAX_BODY_BYTES", str(_DEFAULT_MAX_BODY_BYTES))
            ),
            log_level=os.environ.get("WEBHOOK_LOG_LEVEL", "info").lower(),
            enable_simulator=os.environ.get("WEBHOOK_ENABLE_SIMULATOR", "").strip().lower()
            in ("1", "true", "yes"),
            beads_enabled=os.environ.get("BEADS_ENABLED", "true").strip().lower()
            in ("1", "true", "yes"),
            beads_poll_interval=int(os.environ.get("BEADS_POLL_INTERVAL", "10")),
            beads_max_retries=int(os.environ.get("BEADS_MAX_RETRIES", "3")),
            beads_workspace_root=os.environ.get("BEADS_WORKSPACE_ROOT", "/workspace"),
            dashboard_token=(os.environ.get("DASHBOARD_TOKEN", "").strip() or None),
            # Legacy wall-clock timeout (kept for backward compat; feeds
            # hard_ceiling_secs when HARD_CEILING_SECS is not set).
            dispatch_timeout=_parse_optional_int("DISPATCH_TIMEOUT_SECS"),
            idle_timeout_secs=int(os.environ.get("IDLE_TIMEOUT_SECS", "900")),
            error_grace_secs=int(os.environ.get("ERROR_GRACE_SECS", "300")),
            hard_ceiling_secs=(
                _parse_optional_int("HARD_CEILING_SECS")
                or _parse_optional_int("DISPATCH_TIMEOUT_SECS")
                or 5400
            ),
            watchdog_poll_secs=int(os.environ.get("WATCHDOG_POLL_SECS", "30")),
            max_consecutive_errors=int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "5")),
            watchdog_debug=os.environ.get("WATCHDOG_DEBUG", "").strip().lower()
            in ("1", "true", "yes"),
            permission_ask_grace_secs=int(os.environ.get("PERMISSION_ASK_GRACE_SECS", "60")),
            server_log_path=os.environ.get(
                "OPENCODE_SERVER_LOG_PATH",
                "/home/app/.local/share/opencode/log/opencode.log",
            ),
            sentry_dsn=(os.environ.get("SENTRY_DSN", "").strip() or None),
            sentry_environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
            sentry_traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            sentry_release=(os.environ.get("SENTRY_RELEASE", "").strip() or None),
        )
