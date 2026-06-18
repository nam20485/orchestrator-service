from __future__ import annotations

import logging
import os
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

    # Unique per-dispatch files so concurrent webhooks don't clobber each other.
    fd, prompt_name = tempfile.mkstemp(prefix="prompt-", suffix=".md", dir=log_dir)
    prompt_path = Path(prompt_name)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    cmd = _prompt_script_invocation(settings, prompt_path)

    logger.info(
        "Dispatching orchestration run server=%s workspace=%s script=%s prompt_bytes=%s",
        settings.opencode_server_url,
        settings.workspace,
        settings.prompt_script,
        len(prompt.encode("utf-8")),
    )

    stderr_path = log_dir / f"{prompt_path.stem}.stderr"
    stderr_file = open(stderr_path, "w", encoding="utf-8")
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            start_new_session=True,
        )
    finally:
        # The child inherited the FD; close the parent-side handle to avoid leaks.
        stderr_file.close()
