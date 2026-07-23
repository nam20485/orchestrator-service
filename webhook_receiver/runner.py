from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from webhook_receiver.config import Settings
from webhook_receiver.event_store import EventStore
from webhook_receiver.filters import should_filter
from webhook_receiver.run_stream import extract_tool_names
from webhook_receiver.watchdog import (
    REASON_CONSECUTIVE_ERRORS,
    REASON_HARD_CEILING,
    REASON_IDLE_TIMEOUT,
    IdleWatchdog,
    WatchdogConfig,
    WatchdogState,
)

logger = logging.getLogger(__name__)

# ── Run-completion tracing ─────────────────────────────────────────────────
# The opencode client streams each tool invocation as a leading "glyph" + tool
# name, e.g. ``⚙ memory-graph_create_entities``, ``% WebFetch``, ``→ Read``.
# We scan the captured stderr to classify the run: a dispatch that exits cleanly
# (status 0) but invoked ONLY planning/reading tools — never bash/task/write/edit
# — almost certainly narrated a plan and self-terminated instead of acting. That
# silent "looks like success" failure is what slipped past the non-zero-exit
# failure comment (see _run_completion_watcher).

# A leading glyph is any single non-alphanumeric, non-space char that is NOT
# JSON/log punctuation (so JSON object/array/string lines don't false-match).
# The glyph/ANSI parsing itself lives in run_stream so the run classifier and
# the dashboard event feed share one decoder (no drift); see extract_tool_names.
# Tools that constitute real work (execution / delegation). If a run invokes any
# of these, it is NOT a zero-work run regardless of what else it did.
_EXECUTION_TOOLS = frozenset({"bash", "task", "write", "edit"})

# Tools that are planning/reading only — safe to use without doing real work.
_PLANNING_LEAF = frozenset({"webfetch", "read", "grep", "glob", "list"})
_PLANNING_PREFIX = (
    "sequential-thinking",
    "memory-graph",
    "web-reader",
    "zread",
    "web-search-prime",
    "web-search",
    "microsoft-learn",
    "microsoft_docs",
)


def _is_planning_tool(name: str) -> bool:
    """True if *name* is a planning/reading tool (i.e. not execution/delegation)."""
    if name in _EXECUTION_TOOLS:
        return False
    return name in _PLANNING_LEAF or any(name.startswith(p) for p in _PLANNING_PREFIX)


# ── Dispatch identity: workflow parsing, slug, run manifest ─────────────────
# Every webhook dispatch is captured as ``<stem>.{md,stdout,stderr}`` plus a
# ``<stem>.manifest.json`` sidecar carrying repo/issue/workflow so a run can be
# found by identity (not by guessing a random tempfile name) and listed in the
# dashboard. The orchestrator prompt body of an ``orchestrate-dynamic-workflow``
# dispatch carries ``$workflow_name = <name>``; we parse it for the manifest.

_WORKFLOW_NAME_RE = re.compile(r"\$workflow_name\s*=\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")


def _parse_workflow_name(prompt: str) -> str | None:
    """Extract ``$workflow_name = X`` from a dispatch prompt body, else None."""
    m = _WORKFLOW_NAME_RE.search(prompt or "")
    return m.group(1) if m else None


# Characters allowed in a dispatch slug's filename. MUST match the set accepted
# by dashboard._valid_run_stem so every produced stem is viewable end-to-end
# (a dotted workflow/repo name must not produce a stem the dashboard rejects).
_SLUG_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def _slug_repo(full_name: str) -> str:
    """Filesystem-safe form of ``owner/repo`` for log filenames/slugs."""
    return _SLUG_SAFE_RE.sub("-", full_name.replace("/", "__"))


def _slug_segment(value: str) -> str:
    """Sanitize a free-form slug segment (e.g. a workflow name) to the safe set."""
    return _SLUG_SAFE_RE.sub("-", value)


def _dispatch_slug(
    ctx: DispatchContext | None, workflow: str | None, ts: str
) -> str:
    """Build a human-readable identity slug for a dispatch's log files.

    Keeps the ``prompt-`` prefix so existing ``prompt-*.md`` globs keep working;
    the slug encodes repo/issue/workflow/timestamp for shell browsing. Every
    segment is reduced to the set dashboard ``_valid_run_stem`` accepts
    (``[A-Za-z0-9_-]``) so a run with a dotted workflow or repo name is still
    viewable end-to-end.
    """
    repo = _slug_repo(ctx.repo_full_name) if ctx else "adhoc"
    issue = f"issue-{ctx.issue_number}" if ctx else "no-issue"
    wf = _slug_segment(workflow) if workflow else "adhoc"
    return f"prompt-{repo}__{issue}__{wf}__{ts}"


def _write_run_manifest(log_dir: Path, stem: str, payload: dict) -> None:
    """Write (or overwrite) the ``<stem>.manifest.json`` sidecar. Best-effort."""
    path = log_dir / f"{stem}.manifest.json"
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        logger.warning("Failed to write run manifest %s", path, exc_info=True)


def _update_run_manifest(
    log_dir: Path, stem: str, completion: dict
) -> None:
    """Merge completion fields (ended_at/exit_code/classification/...) into the
    existing manifest sidecar, starting from whatever was written at dispatch.
    """
    path = log_dir / f"{stem}.manifest.json"
    data: dict = {}
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.update(completion)
    _write_run_manifest(log_dir, stem, data)


def _dispatch_issue_closed(ctx: DispatchContext) -> bool:
    """Best-effort check that the dispatch issue is closed.

    The orchestrator's own success contract for an ``orchestrate-dynamic-workflow``
    dispatch is to **close the dispatch issue on success** (see the orchestrator
    prompt's ``issues/opened`` clause). So an exit-0 run whose dispatch issue is
    still open almost certainly did not finish the workflow. Any error or
    non-JSON response returns ``True`` so we never false-positive an "incomplete".
    """
    cmd = [
        "gh",
        "issue",
        "view",
        str(ctx.issue_number),
        "--repo",
        ctx.repo_full_name,
        "--json",
        "state",
    ]
    env = _gh_env()
    try:
        res = subprocess.run(
            cmd, env=env, capture_output=True, text=True, check=False, timeout=30
        )
        data = json.loads(res.stdout)
        return str(data.get("state", "")).lower() == "closed"
    except Exception:
        logger.warning(
            "Could not determine dispatch issue state for %s#%s",
            ctx.repo_full_name,
            ctx.issue_number,
            exc_info=True,
        )
        return True


def _build_incomplete_body(
    ctx: DispatchContext,
    tools: list[str],
    log_dir: str,
    prompt_stem: str,
) -> str:
    listed = ", ".join(tools) or "none detected"
    return (
        "⚠️ Orchestrator run exited cleanly but the dispatch issue is still "
        "open — the workflow likely did **not** finish (remaining assignments, "
        "post-assignment events, the `orchestration:plan-approved` label, or "
        "PR merge/close may be incomplete).\n\n"
        f"Detected tool calls: `{listed}`\n\n"
        "Compare against the golden-path checklist pattern "
        "(`traces/golden-path-foxtrot54-project-setup.md`): a healthy run "
        "re-prints its `[ ]`→`[x]` checklist after every assignment.\n\n"
        f"Runner logs (`{log_dir}`):\n"
        f"- `{prompt_stem}.stdout`\n"
        f"- `{prompt_stem}.stderr`\n"
    )


def _post_incomplete_comment(
    ctx: DispatchContext,
    tools: list[str],
    log_dir: str,
    prompt_stem: str,
) -> None:
    """Post an advisory comment for an exit-0-but-unfinished run."""
    _post_issue_comment(
        ctx, _build_incomplete_body(ctx, tools, log_dir, prompt_stem)
    )


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
    trigger_label: str | None = None


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
        "-Variant",
        settings.variant,
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


# ── Container-log formatting ───────────────────────────────────────────────
# The opencode server emits slog text-handler lines like:
#   timestamp=2026-07-23T02:17:55.898Z level=INFO run=2127ad56 message=…
# For visual scanning in docker logs the common envelope (timestamp/level/run)
# is grouped into brackets so the variable payload that follows stands out:
#   [timestamp=… level=INFO run=…] message=… key=value …
# Non-slog lines (glyphs, Python logger output, etc.) pass through unchanged.
# The trace file always receives the raw line; only the container logger is
# reformatted — filters and the watchdog must see the original text.
_SLOG_ENVELOPE_RE = re.compile(
    r"^(timestamp=\S+)\s+(level=\S+)(?:\s+(run=\S+))?(?:\s+(.*))?$"
)


def _format_log_line(line: str) -> str:
    """Group the slog envelope (timestamp/level/run) into brackets.

    See ``_SLOG_ENVELOPE_RE`` for the matched pattern. Non-slog lines return
    unchanged.
    """
    m = _SLOG_ENVELOPE_RE.match(line)
    if not m:
        return line
    parts = [m.group(1), m.group(2)]
    if m.group(3):
        parts.append(m.group(3))
    rest = m.group(4) or ""
    if rest:
        return f"[{' '.join(parts)}] {rest}"
    return f"[{' '.join(parts)}]"


def _stream_to_logger_and_file(
    pipe, file_handle, label: str, state: WatchdogState | None = None
) -> None:
    """Read lines from *pipe*, write each to *file_handle* and log at INFO.

    Lines matching the trace blacklist are written to the file but suppressed
    from the logger so container output stays clean. When *state* is provided,
    every line (filtered or not) updates the :class:`WatchdogState` so the idle
    watchdog has an accurate activity signal.
    """
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            file_handle.write(line)
            file_handle.flush()
            if state is not None:
                state.record_line(line)
            if not should_filter(line):
                logger.info(
                    "[%s] %s", label, _format_log_line(line.rstrip())
                )
    except ValueError:
        pass  # pipe closed


# ── Secret sanitization for GitHub issue comments ──────────────────────────
# Patterns for common credentials that may appear in error messages. These are
# scrubbed before any error detail is posted to a public GitHub issue comment
# to prevent accidental secret leakage from CLI stderr output.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),  # GitHub PATs
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style API keys
    re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{20,}"),  # Bearer tokens
    re.compile(
        r"(?i)(password|passwd|secret|api[_-]?key|token|auth)"
        r"\s*[:=]\s*\S+"
    ),  # key=value assignments
]


def _sanitize_for_comment(text: str) -> str:
    """Scrub common credential patterns from *text* before posting to GitHub.

    Replaces matches with ``[REDACTED]``. This is a best-effort filter — it is
    not a substitute for proper secret management, but it prevents the most
    common credential formats from leaking into public issue comments.
    """
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def _build_failure_body(
    ctx: DispatchContext,
    exit_code: int,
    log_dir: str,
    prompt_stem: str,
    timed_out: bool = False,
    kill_reason: str | None = None,
    consecutive_errors: int = 0,
    last_error_message: str = "",
) -> str:
    """Build the failure comment body for a non-zero/killed/timeout exit.

    When *kill_reason* is set (watchdog kill), the message is tailored to the
    specific condition: idle timeout, hard ceiling, or consecutive errors.
    """
    if kill_reason == REASON_IDLE_TIMEOUT:
        reason = "went idle (no output from the orchestrator CLI)"
    elif kill_reason == REASON_HARD_CEILING:
        reason = "hit the hard runtime ceiling"
    elif kill_reason == REASON_CONSECUTIVE_ERRORS:
        reason = f"hit {consecutive_errors} consecutive errors"
    elif timed_out:
        reason = "timed out"
    else:
        reason = f"exited with status {exit_code}"

    body = (
        f"❌ Orchestrator run did not complete ({reason}).\n\n"
        f"Runner logs (`{log_dir}`):\n"
        f"- `{prompt_stem}.stdout`\n"
        f"- `{prompt_stem}.stderr`\n"
    )
    if kill_reason == REASON_CONSECUTIVE_ERRORS and last_error_message:
        body += f"\nLast error: `{_sanitize_for_comment(last_error_message[:200])}`\n"
    return body


def _build_zero_work_body(
    ctx: DispatchContext,
    tools: set[str],
    log_dir: str,
    prompt_stem: str,
) -> str:
    listed = ", ".join(sorted(tools)) or "none detected"
    return (
        "⚠️ Orchestrator run exited cleanly (status 0) but executed **no work "
        "tools** (no `bash`/`task`/`write`/`edit`).\n\n"
        f"Detected tool calls (planning/reading only): `{listed}`\n\n"
        "This usually means the agent narrated a plan instead of acting and "
        "self-terminated early. No code/issue/PR changes were made. See the "
        "runner logs and retry.\n\n"
        f"Runner logs (`{log_dir}`):\n"
        f"- `{prompt_stem}.stdout`\n"
        f"- `{prompt_stem}.stderr`\n"
    )


def _gh_env() -> dict[str, str]:
    """Env for gh CLI calls: host env + GH_TOKEN from the orchestration PAT.

    Single source of truth for the token the runner uses to talk to GitHub
    (issue-state checks + issue comments). Centralizes the precedence
    (``GH_ORCHESTRATION_AGENT_TOKEN`` then ``GITHUB_TOKEN``) so the two callers
    cannot drift.
    """
    env = os.environ.copy()
    token = os.environ.get("GH_ORCHESTRATION_AGENT_TOKEN") or os.environ.get(
        "GITHUB_TOKEN"
    )
    if token:
        env["GH_TOKEN"] = token
    return env


def _post_issue_comment(ctx: DispatchContext, body: str) -> None:
    """Post *body* as a comment on the triggering issue via ``gh issue comment``.

    Best-effort: any error is logged and swallowed so it can never crash the
    completion watcher thread. Uses ``GH_ORCHESTRATION_AGENT_TOKEN`` (the PAT
    the agent uses for orchestration) falling back to ``GITHUB_TOKEN``.
    """
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
    try:
        subprocess.run(
            cmd,
            input=body,
            env=_gh_env(),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        logger.warning(
            "Failed to post issue comment for issue %s in %s",
            ctx.issue_number,
            ctx.repo_full_name,
            exc_info=True,
        )


def _post_failure_comment(
    ctx: DispatchContext,
    exit_code: int,
    log_dir: str,
    prompt_stem: str,
    timed_out: bool = False,
    kill_reason: str | None = None,
    consecutive_errors: int = 0,
    last_error_message: str = "",
) -> None:
    """Post a failure comment on the triggering issue (non-zero/killed/timeout)."""
    _post_issue_comment(
        ctx,
        _build_failure_body(
            ctx,
            exit_code,
            log_dir,
            prompt_stem,
            timed_out,
            kill_reason=kill_reason,
            consecutive_errors=consecutive_errors,
            last_error_message=last_error_message,
        ),
    )


def _post_zero_work_comment(
    ctx: DispatchContext,
    tools: set[str],
    log_dir: str,
    prompt_stem: str,
) -> None:
    """Post an advisory comment when a run exited 0 but did no work tools."""
    _post_issue_comment(ctx, _build_zero_work_body(ctx, tools, log_dir, prompt_stem))


def _run_completion_watcher(
    proc: subprocess.Popen,
    event_store: EventStore | None,
    dispatch_ctx: DispatchContext | None,
    log_dir: str,
    prompt_stem: str,
    timeout: int | None = None,
    stderr_thread: threading.Thread | None = None,
    state: WatchdogState | None = None,
    watchdog_config: WatchdogConfig | None = None,
) -> None:
    """Wait for the dispatched run to finish, then emit events + post a failure
    comment on a non-zero/killed/timeout exit.

    When *state* and *watchdog_config* are provided, an :class:`IdleWatchdog`
    monitors the process for activity and kills it on idle / consecutive
    errors / hard ceiling. Otherwise the legacy ``proc.wait(timeout=...)``
    path is used (backward compat for existing tests and non-dispatch callers).

    Additionally traces the run: a clean (status 0) exit that invoked only
    planning/reading tools — never ``bash``/``task``/``write``/``edit`` — is
    flagged as a "zero-work" run (the agent narrated a plan and
    self-terminated) and gets an advisory comment on the triggering issue.
    """
    timed_out = False
    kill_reason: str | None = None
    wd_consecutive_errors = 0
    wd_last_error_message = ""

    stderr_path = Path(log_dir) / f"{prompt_stem}.stderr"

    if state is not None and watchdog_config is not None:
        # ── Idle-watchdog path (production dispatch) ──────────────────────
        logger.info(
            "[watchdog] starting pid=%s idle_timeout=%ds hard_ceiling=%ss "
            "poll=%ds max_errors=%d debug=%s",
            proc.pid,
            watchdog_config.idle_timeout_secs,
            watchdog_config.hard_ceiling_secs,
            watchdog_config.poll_interval_secs,
            watchdog_config.max_consecutive_errors,
            watchdog_config.debug,
        )
        try:
            wd = IdleWatchdog(proc, state, watchdog_config, stderr_path)
            result = wd.run()
            exit_code = (
                result.exit_code
                if result.exit_code is not None
                else (proc.returncode if proc.returncode is not None else -1)
            )
            if result.killed:
                kill_reason = result.reason
                timed_out = result.reason in (
                    REASON_IDLE_TIMEOUT,
                    REASON_HARD_CEILING,
                )
                wd_consecutive_errors = result.consecutive_errors
                wd_last_error_message = result.last_error_message
        except Exception:
            logger.exception("Watchdog error; falling back to bounded wait")
            # Use the hard ceiling as a backstop so a watchdog crash doesn't
            # leave the process running forever. If the wait times out, kill
            # the process — the safety net must still hold.
            fallback_timeout = watchdog_config.hard_ceiling_secs or 5400
            try:
                proc.wait(timeout=fallback_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Fallback wait timed out after %ds; killing process",
                    fallback_timeout,
                )
                try:
                    proc.kill()
                except Exception:
                    logger.exception("Fallback proc.kill() failed")
                proc.wait()
            except Exception:
                logger.exception("Fallback proc.wait() also failed")
            exit_code = proc.returncode if proc.returncode is not None else -1
    else:
        # ── Legacy path (backward compat for tests / non-watchdog callers) ──
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
        exit_code = proc.returncode if proc.returncode is not None else -1

    failed = exit_code != 0

    if failed and dispatch_ctx is not None:
        _post_failure_comment(
            dispatch_ctx,
            exit_code,
            str(log_dir),
            prompt_stem,
            timed_out,
            kill_reason=kill_reason,
            consecutive_errors=wd_consecutive_errors,
            last_error_message=wd_last_error_message,
        )

    # Tracing: classify a clean exit by the tools it actually invoked. A run
    # that used only planning/reading tools did no real work and is almost
    # certainly a narrate-and-self-terminate (the silent "looks like success"
    # failure mode). Post an advisory comment so it is not silently lost.
    tools: set[str] = set()
    zero_work = False
    if not failed:
        stderr_path = Path(log_dir) / f"{prompt_stem}.stderr"
        try:
            if stderr_thread is not None:
                stderr_thread.join(timeout=15)
            if stderr_path.exists():
                tools = extract_tool_names(
                    stderr_path.read_text(encoding="utf-8", errors="replace")
                )
        except Exception:
            logger.warning(
                "Failed to read orchestrator stderr for run analysis",
                exc_info=True,
            )
            tools = set()
        zero_work = bool(tools) and all(_is_planning_tool(t) for t in tools)
        logger.info(
            "Run summary exit_code=%s zero_work=%s tools=%s",
            exit_code,
            zero_work,
            sorted(tools),
        )
        if zero_work and dispatch_ctx is not None:
            _post_zero_work_comment(
                dispatch_ctx, tools, str(log_dir), prompt_stem
            )

    # Incomplete-run detection: a clean, non-zero-work exit whose dispatch
    # issue is still open did not satisfy the orchestrator's own success
    # contract (close the dispatch issue on success). The gap-miner run hit
    # exactly this — exit 0, real tools used, but the workflow was abandoned
    # partway and Issue #1 stayed open with no comment.
    #
    # The close-on-success contract applies to every prompt clause that closes
    # the triggering issue on success and publishes work with best-effort steps
    # that leave the issue open on push/PR failure — currently
    # ``orchestration:dispatch`` and ``gh-issue-tracking:direct-body``. Other
    # labels (``orchestration:plan-approved``, ``epic-ready``, …) succeed by
    # creating an epic and skip to ##Final WITHOUT closing the issue, so
    # probing their state would false-positive. Gate the check on that label
    # set.
    _CLOSE_ON_SUCCESS_LABELS = frozenset(
        {"orchestration:dispatch", "gh-issue-tracking:direct-body"}
    )
    incomplete = False
    if (
        not failed
        and not zero_work
        and dispatch_ctx is not None
        and (dispatch_ctx.trigger_label or "").lower() in _CLOSE_ON_SUCCESS_LABELS
    ):
        if not _dispatch_issue_closed(dispatch_ctx):
            incomplete = True
            _post_incomplete_comment(
                dispatch_ctx, sorted(tools), str(log_dir), prompt_stem
            )

    classification = (
        kill_reason if kill_reason is not None
        else "failed" if failed
        else "zero_work" if zero_work
        else "incomplete" if incomplete
        else "completed"
    )
    _update_run_manifest(
        Path(log_dir),
        prompt_stem,
        {
            "ended_at": datetime.now(UTC).isoformat(),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "kill_reason": kill_reason,
            "classification": classification,
            "consecutive_errors": wd_consecutive_errors,
            "tools": sorted(tools),
        },
    )

    if event_store is not None:
        if failed:
            event_store.emit(
                "dispatch_failed",
                exit_code=exit_code,
                prompt_file=f"{prompt_stem}.md",
                timed_out=timed_out,
            )
        elif zero_work:
            event_store.emit(
                "dispatch_zero_work",
                exit_code=exit_code,
                prompt_file=f"{prompt_stem}.md",
                tools=sorted(tools),
            )
        elif incomplete:
            event_store.emit(
                "dispatch_incomplete",
                exit_code=exit_code,
                prompt_file=f"{prompt_stem}.md",
                tools=sorted(tools),
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
) -> str:
    """Run the prompt script in the background (non-blocking for the HTTP handler).

    Returns the prompt file stem (``<slug>-<rand>``) so callers can correlate
    the dispatch with run logs and the webhooks trace page.
    """
    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # Identity: derive repo/issue/workflow so log files + the manifest sidecar
    # are findable by dispatch identity, not by a random tempfile name. The
    # ``prompt-`` prefix is kept so existing ``prompt-*.md`` globs keep working.
    workflow = _parse_workflow_name(prompt)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = _dispatch_slug(dispatch_ctx, workflow, ts)

    # Unique per-dispatch files so concurrent webhooks don't clobber each other.
    fd, prompt_name = tempfile.mkstemp(prefix=f"{slug}-", suffix=".md", dir=log_dir)
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

    # Run manifest sidecar: identity + lifecycle metadata for the dashboard's
    # "orchestration runs" view. Completion fields are merged in by the watcher.
    _write_run_manifest(
        log_dir,
        prompt_path.stem,
        {
            "stem": prompt_path.stem,
            "repo_full_name": dispatch_ctx.repo_full_name if dispatch_ctx else None,
            "issue_number": dispatch_ctx.issue_number if dispatch_ctx else None,
            "html_url": dispatch_ctx.html_url if dispatch_ctx else None,
            "workflow": workflow,
            "prompt_file": prompt_path.name,
            "pid": proc.pid,
            "started_at": ts,
            "model": settings.model,
            "agent": settings.agent,
            "log_dir": str(log_dir),
        },
    )

    if event_store:
        event_store.emit(
            "dispatch_started",
            prompt_file=prompt_path.name,
            pid=proc.pid,
        )

    # ── Idle watchdog state ───────────────────────────────────────────────
    # A single WatchdogState is shared between the stdout/stderr reader
    # threads and the completion watcher's IdleWatchdog. The stream readers
    # call state.record_line() on every line so the watchdog has an accurate
    # activity signal.
    wd_state = WatchdogState(time.monotonic())
    wd_config = WatchdogConfig.from_settings(settings)

    # Stream stdout and stderr to both logger and files via daemon threads.
    # Both threads update the shared WatchdogState so the idle watchdog tracks
    # any output from either stream.
    threading.Thread(
        target=_stream_to_logger_and_file,
        args=(proc.stdout, stdout_file, "opencode", wd_state),
        daemon=True,
    ).start()
    stderr_thread = threading.Thread(
        target=_stream_to_logger_and_file,
        args=(proc.stderr, stderr_file, "opencode-err", wd_state),
        daemon=True,
    )
    stderr_thread.start()

    # Watcher: the IdleWatchdog monitors the process for activity and kills it
    # on idle / consecutive errors / hard ceiling. After exit, the watcher
    # classifies the run, posts failure/zero-work/incomplete comments, and
    # writes the run manifest. Always started (even without an event_store) so
    # a failed/zero-work run leaves a diagnosable comment.
    def _watch() -> None:
        _run_completion_watcher(
            proc,
            event_store,
            dispatch_ctx,
            str(log_dir),
            prompt_path.stem,
            settings.dispatch_timeout,
            stderr_thread=stderr_thread,
            state=wd_state,
            watchdog_config=wd_config,
        )

    threading.Thread(target=_watch, daemon=True).start()

    return prompt_path.stem
