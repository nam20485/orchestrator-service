from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from webhook_receiver.config import Settings
from webhook_receiver.event_store import EventStore
from webhook_receiver.filters import should_filter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchContext:
    """Identity of the webhook event that triggered a dispatch.

    Carried into the completion watcher so a non-zero/killed run can post a
    failure comment on the triggering issue (mirrors the golden-path GHA
    ``if: failure()`` step). ``None`` means no issue is attributable and the
    failure comment is skipped.
    """

    repo_full_name: str
    issue_number: int
    html_url: str | None = None


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


def _build_failure_body(
    ctx: DispatchContext,
    exit_code: int,
    log_dir: str,
    prompt_stem: str,
    timed_out: bool = False,
) -> str:
    reason = "timed out" if timed_out else f"exited with status {exit_code}"
    return (
        f"❌ Orchestrator run did not complete ({reason}).\n\n"
        f"Runner logs (`{log_dir}`):\n"
        f"- `{prompt_stem}.stdout`\n"
        f"- `{prompt_stem}.stderr`\n"
    )


def _post_failure_comment(
    ctx: DispatchContext,
    exit_code: int,
    log_dir: str,
    prompt_stem: str,
    timed_out: bool = False,
) -> None:
    """Post a failure comment on the triggering issue via ``gh issue comment``.

    Best-effort: any error is logged and swallowed so it can never crash the
    completion watcher thread. Uses ``GH_ORCHESTRATION_AGENT_TOKEN`` (the PAT
    the agent uses for orchestration) falling back to ``GITHUB_TOKEN``.
    """
    body = _build_failure_body(ctx, exit_code, log_dir, prompt_stem, timed_out)
    cmd = [
        "gh",
        "issue",
        "comment",
        str(ctx.issue_number),
        "--repo",
        ctx.repo_full_name,
        "--body-file",
        "-",
    ]
    env = os.environ.copy()
    token = os.environ.get("GH_ORCHESTRATION_AGENT_TOKEN") or os.environ.get(
        "GITHUB_TOKEN"
    )
    if token:
        env["GH_TOKEN"] = token
    try:
        subprocess.run(
            cmd,
            input=body,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        logger.warning(
            "Failed to post failure comment for issue %s in %s",
            ctx.issue_number,
            ctx.repo_full_name,
            exc_info=True,
        )


def _run_completion_watcher(
    proc: subprocess.Popen,
    event_store: EventStore | None,
    dispatch_ctx: DispatchContext | None,
    log_dir: str,
    prompt_stem: str,
    timeout: int | None = None,
) -> None:
    """Wait for the dispatched run to finish, then emit events + post a failure
    comment on a non-zero/killed/timeout exit.

    Factored out of the daemon thread so it is directly unit-testable with a
    mock ``proc``. A timeout (``DISPATCH_TIMEOUT_SECS``) kills the process and
    treats the result as a failure.
    """
    timed_out = False
    try:
        if timeout is not None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
        else:
            proc.wait()
    except Exception:
        logger.exception("Completion watcher error while waiting for pid")

    exit_code = proc.returncode
    failed = exit_code != 0

    if failed and dispatch_ctx is not None:
        _post_failure_comment(
            dispatch_ctx, exit_code, str(log_dir), prompt_stem, timed_out
        )

    if event_store is not None:
        if failed:
            event_store.emit(
                "dispatch_failed",
                exit_code=exit_code,
                prompt_file=f"{prompt_stem}.md",
                timed_out=timed_out,
            )
        else:
            event_store.emit(
                "dispatch_completed",
                exit_code=exit_code,
                prompt_file=f"{prompt_stem}.md",
            )


def dispatch_to_opencode(
    settings: Settings,
    prompt: str,
    event_store: EventStore | None = None,
    dispatch_ctx: DispatchContext | None = None,
) -> None:
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

    if event_store:
        event_store.emit(
            "dispatch_started",
            prompt_file=prompt_path.name,
            pid=proc.pid,
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

    # Watcher: wait for process completion, emit events, and post a failure
    # comment on a non-zero/killed/timeout exit. Always started (even without
    # an event_store) so a failed run leaves a diagnosable issue comment.
    def _watch() -> None:
        _run_completion_watcher(
            proc,
            event_store,
            dispatch_ctx,
            log_dir,
            prompt_path.stem,
            settings.dispatch_timeout,
        )

    threading.Thread(target=_watch, daemon=True).start()
