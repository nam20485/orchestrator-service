from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from webhook_receiver.config import Settings
from webhook_receiver.filters import should_filter

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


def _stream_to_logger_and_file(
    pipe, file_handle, label: str
) -> None:
    """Read lines from *pipe*, write each to *file_handle* and log at INFO.

    Lines matching the trace blacklist are written to the file but suppressed
    from the logger so container output stays clean.
    """
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            file_handle.write(line)
            file_handle.flush()
            if not should_filter(line):
                logger.info("[%s] %s", label, line.rstrip())
    except ValueError:
        pass  # pipe closed


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
    logger.debug("Dispatch command: %s", " ".join(cmd))

    stdout_path = log_dir / f"{prompt_path.stem}.stdout"
    stderr_path = log_dir / f"{prompt_path.stem}.stderr"
    stdout_file = open(stdout_path, "w", encoding="utf-8")
    stderr_file = open(stderr_path, "w", encoding="utf-8")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )

    logger.info(
        "Started orchestration run pid=%s prompt=%s stdout_log=%s stderr_log=%s",
        proc.pid,
        prompt_path.name,
        stdout_path,
        stderr_path,
    )

    # Stream stdout and stderr to both logger and files via daemon threads.
    threading.Thread(
        target=_stream_to_logger_and_file,
        args=(proc.stdout, stdout_file, "opencode"),
        daemon=True,
    ).start()
    threading.Thread(
        target=_stream_to_logger_and_file,
        args=(proc.stderr, stderr_file, "opencode-err"),
        daemon=True,
    ).start()
