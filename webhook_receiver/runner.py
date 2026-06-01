from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from webhook_receiver.config import Settings

logger = logging.getLogger(__name__)


def _base_args(settings: Settings) -> list[str]:
    return [
        "-ServerUrl",
        settings.opencode_server_url,
        "-Workspace",
        settings.workspace,
        "-Model",
        settings.model,
        "-Agent",
        settings.agent,
    ]


def _prompt_script_invocation(settings: Settings, prompt_path: Path) -> list[str]:
    script = settings.prompt_script
    if script.suffix.lower() != ".ps1":
        raise ValueError(f"PROMPT_SCRIPT must be a PowerShell script (.ps1): {script}")
    return [
        "pwsh",
        "-NoProfile",
        "-File",
        str(script),
        *_base_args(settings),
        "-PromptFile",
        str(prompt_path),
    ]


def dispatch_to_opencode(settings: Settings, prompt: str) -> None:
    """Run the prompt script in the background (non-blocking for the HTTP handler)."""
    log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
    log_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = log_dir / "last-prompt.md"
    _ = prompt_path.write_text(prompt, encoding="utf-8")

    cmd = _prompt_script_invocation(settings, prompt_path)

    logger.info(
        "Dispatching orchestration run server=%s workspace=%s script=%s prompt_bytes=%s",
        settings.opencode_server_url,
        settings.workspace,
        settings.prompt_script,
        len(prompt.encode("utf-8")),
    )

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=open(log_dir / "last-run.stderr", "w", encoding="utf-8"),
        start_new_session=True,
    )
