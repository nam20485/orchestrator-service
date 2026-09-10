from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver.config import Settings
from webhook_receiver.run_stream import extract_tool_names
from webhook_receiver.runner import (
    DispatchContext,
    _base_args,
    _build_failure_body,
    _dispatch_issue_closed,
    _dispatch_slug,
    _format_log_line,
    _is_planning_tool,
    _parse_workflow_name,
    _prompt_script_invocation,
    _run_completion_watcher,
    _sanitize_for_comment,
    _stream_to_logger_and_file,
    _update_run_manifest,
    _write_run_manifest,
    dispatch_to_opencode,
)
from webhook_receiver.watchdog import (
    REASON_CONSECUTIVE_ERRORS,
    REASON_IDLE_TIMEOUT,
    REASON_PERMISSION_DEADLOCK,
    WatchdogConfig,
    WatchdogState,
)


def _test_settings(**overrides: object) -> Settings:
    repo = Path(__file__).resolve().parent.parent
    defaults = dict(
        host="127.0.0.1",
        port=8080,
        github_webhook_secret="test-secret",
        opencode_server_url="http://localhost:4099",
        prompt_script=repo / "scripts" / "prompt.ps1",
        workspace="/workspace",
        model="zai-coding-plan/glm-4.7-flash",
        agent="orchestrator",
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ── _base_args ────────────────────────────────────────────────────────────


def test_base_args_builds_correct_pwsh_args() -> None:
    settings = _test_settings(
        opencode_server_url="http://srv:4099",
        workspace="/ws",
        model="m",
        agent="a",
        variant="high",
    )
    args = _base_args(settings)
    assert "-ServerUrl" in args
    assert "http://srv:4099" in args
    assert "-Workspace" in args
    assert "/ws" in args
    assert "-Model" in args
    assert "m" in args
    assert "-Agent" in args
    assert "a" in args
    assert "-Variant" in args
    assert "high" in args


# ── _prompt_script_invocation ─────────────────────────────────────────────


def test_prompt_script_invocation_valid_ps1(tmp_path: Path) -> None:
    script = tmp_path / "prompt.ps1"
    settings = _test_settings(prompt_script=script)
    prompt_path = tmp_path / "prompt.md"
    cmd = _prompt_script_invocation(settings, prompt_path)
    assert cmd[0] == "pwsh"
    assert "-NoProfile" in cmd
    assert "-File" in cmd
    assert str(script) in cmd
    assert "-PromptFile" in cmd
    assert str(prompt_path) in cmd


def test_prompt_script_invocation_rejects_non_ps1(tmp_path: Path) -> None:
    script = tmp_path / "prompt.sh"
    settings = _test_settings(prompt_script=script)
    with pytest.raises(ValueError, match="PowerShell"):
        _prompt_script_invocation(settings, tmp_path / "p.md")


def test_prompt_script_invocation_carries_worktree_workspace(tmp_path: Path) -> None:
    """The beads loop sets workspace=<worktree>; the dispatched cmd must carry it
    as -Workspace so the agent runs inside the worktree (not a re-derived slug).
    Regression guard for the beads-loop → prompt.ps1 worktree hand-off.
    """
    worktree = "/workspace/my-app/.worktrees/my-app-a1b2"
    settings = _test_settings(prompt_script=tmp_path / "prompt.ps1", workspace=worktree)
    cmd = _prompt_script_invocation(settings, tmp_path / "prompt.md")
    assert "-Workspace" in cmd
    assert cmd[cmd.index("-Workspace") + 1] == worktree


# ── _format_log_line ──────────────────────────────────────────────────────


def test_format_log_line_groups_envelope_with_run() -> None:
    line = (
        "timestamp=2026-07-23T02:17:55.898Z level=INFO run=2127ad56 "
        'message="llm runtime selected" llm.runtime=ai-sdk'
    )
    assert _format_log_line(line) == (
        "[timestamp=2026-07-23T02:17:55.898Z level=INFO run=2127ad56] "
        'message="llm runtime selected" llm.runtime=ai-sdk'
    )


def test_format_log_line_groups_envelope_bare_message() -> None:
    line = (
        "timestamp=2026-07-23T02:18:11.428Z level=INFO run=2127ad56 "
        "message=evaluated permission=todowrite pattern=*"
    )
    assert _format_log_line(line) == (
        "[timestamp=2026-07-23T02:18:11.428Z level=INFO run=2127ad56] "
        "message=evaluated permission=todowrite pattern=*"
    )


def test_format_log_line_without_run() -> None:
    line = "timestamp=2026-07-23T02:00:00Z level=ERROR message=\"stream error\""
    assert _format_log_line(line) == (
        "[timestamp=2026-07-23T02:00:00Z level=ERROR] "
        'message="stream error"'
    )


def test_format_log_line_envelope_only() -> None:
    line = "timestamp=2026-07-23T02:00:00Z level=INFO run=abc123"
    assert _format_log_line(line) == (
        "[timestamp=2026-07-23T02:00:00Z level=INFO run=abc123]"
    )


def test_format_log_line_non_slog_passthrough() -> None:
    assert _format_log_line("line one") == "line one"
    assert _format_log_line("normal log line") == "normal log line"
    assert _format_log_line("") == ""
    assert _format_log_line("⚙ bash") == "⚙ bash"


# ── _stream_to_logger_and_file ────────────────────────────────────────────


def test_stream_to_logger_writes_to_file_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log_file = tmp_path / "out.log"
    pipe = io.StringIO("line one\nline two\n")
    with log_file.open("w") as fh:
        with caplog.at_level(logging.INFO, logger="webhook_receiver.runner"):
            _stream_to_logger_and_file(pipe, fh, "test")

    content = log_file.read_text()
    assert "line one" in content
    assert "line two" in content
    log_msgs = [r for r in caplog.records if r.name == "webhook_receiver.runner"]
    assert any("line one" in r.getMessage() for r in log_msgs)
    assert any("line two" in r.getMessage() for r in log_msgs)


def test_stream_to_logger_suppresses_filtered_lines(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    log_file = tmp_path / "out.log"
    filtered_line = "service=bus type=message.part.delta data"
    normal_line = "normal log line"
    pipe = io.StringIO(f"{filtered_line}\n{normal_line}\n")
    with log_file.open("w") as fh:
        with caplog.at_level(logging.INFO, logger="webhook_receiver.runner"):
            _stream_to_logger_and_file(pipe, fh, "test")

    content = log_file.read_text()
    assert filtered_line in content
    assert normal_line in content
    log_msgs = [r.getMessage() for r in caplog.records if r.name == "webhook_receiver.runner"]
    assert any(normal_line in m for m in log_msgs)
    assert not any(filtered_line in m for m in log_msgs)


# ── dispatch_to_opencode ──────────────────────────────────────────────────


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_creates_temp_prompt_file(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_popen.return_value = mock_proc
    settings = _test_settings(workspace=str(tmp_path))
    prompt = "Test prompt content"

    dispatch_to_opencode(settings, prompt)

    import glob
    import tempfile

    log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
    prompt_files = sorted(glob.glob(str(log_dir / "prompt-*.md")))
    matching = [f for f in prompt_files if prompt in Path(f).read_text()]
    assert len(matching) >= 1


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_spawns_subprocess_with_correct_cmd(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    mock_proc = MagicMock()
    mock_proc.pid = 999
    mock_popen.return_value = mock_proc
    script = tmp_path / "prompt.ps1"
    settings = _test_settings(prompt_script=script)

    dispatch_to_opencode(settings, "hello")

    assert mock_popen.called
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "pwsh"
    assert str(script) in cmd
    assert "-PromptFile" in cmd


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_starts_streaming_threads(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    mock_proc = MagicMock()
    mock_proc.pid = 42
    mock_popen.return_value = mock_proc
    settings = _test_settings(prompt_script=tmp_path / "prompt.ps1")

    dispatch_to_opencode(settings, "prompt")

    targets = [c.kwargs.get("target") or c.args[0] for c in mock_thread.call_args_list]
    streaming = [t for t in targets if t is _stream_to_logger_and_file]
    assert len(streaming) == 2


# ── DispatchContext + failure comment (T2.1) ───────────────────────────────


def _mock_proc(returncode: int) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.wait = MagicMock()
    proc.kill = MagicMock()
    return proc


def _ctx() -> DispatchContext:
    return DispatchContext(
        repo_full_name="owner/repo",
        issue_number=7,
        trigger_label="orchestration:dispatch",
    )


def _closed_state_run() -> subprocess.CompletedProcess:
    """A gh `issue view --json state` response reporting the issue is closed.

    Used so the incomplete-detection state probe resolves cleanly in tests
    instead of raising on a MagicMock stdout.
    """
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"state":"closed"}'
    )


def _no_comment_posted(mock_run: MagicMock) -> bool:
    """True if no recorded ``subprocess.run`` call was an ``issue comment``.

    A clean exit now probes the dispatch issue state via ``gh issue view``
    (incomplete-run detection), so ``mock_run`` may be called — but a *comment*
    must never be posted on a completed run. This asserts exactly that.
    """
    for call in mock_run.call_args_list:
        cmd = call.args[0] if call.args else call[0]
        if "comment" in cmd:
            return False
    return True


@patch("webhook_receiver.runner.subprocess.run")
def test_failure_comment_posted_on_nonzero_exit(
    mock_run: MagicMock,
) -> None:
    proc = _mock_proc(returncode=1)
    store = MagicMock()
    _run_completion_watcher(proc, store, _ctx(), "/tmp/x", "prompt-abc")

    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gh"
    assert "comment" in cmd
    assert "7" in cmd
    assert "--repo" in cmd
    assert "owner/repo" in cmd
    assert "--body-file" in cmd
    body = mock_run.call_args.kwargs["input"]
    assert "did not complete" in body
    assert "prompt-abc.stdout" in body


@patch("webhook_receiver.runner.subprocess.run")
def test_no_failure_comment_on_zero_exit(
    mock_run: MagicMock,
) -> None:
    mock_run.return_value = _closed_state_run()
    proc = _mock_proc(returncode=0)
    store = MagicMock()
    _run_completion_watcher(proc, store, _ctx(), "/tmp/x", "prompt-abc")

    assert _no_comment_posted(mock_run)
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="prompt-abc.md"
    )


@patch("webhook_receiver.runner.subprocess.run")
def test_no_failure_comment_when_dispatch_ctx_none(
    mock_run: MagicMock,
) -> None:
    proc = _mock_proc(returncode=2)
    store = MagicMock()
    _run_completion_watcher(proc, store, None, "/tmp/x", "prompt-abc")

    assert not mock_run.called
    store.emit.assert_called_once_with(
        "dispatch_failed", exit_code=2, prompt_file="prompt-abc.md", timed_out=False
    )


@patch("webhook_receiver.runner.subprocess.run")
def test_failure_comment_swallows_gh_error(
    mock_run: MagicMock,
) -> None:
    """A failing gh call must never crash the completion watcher."""
    mock_run.side_effect = OSError("gh exploded")
    proc = _mock_proc(returncode=3)
    store = MagicMock()

    # Should not raise.
    _run_completion_watcher(proc, store, _ctx(), "/tmp/x", "prompt-abc")

    assert mock_run.called
    store.emit.assert_called_once()


@patch("webhook_receiver.runner.subprocess.run")
def test_dispatch_timeout_kills_and_comments(
    mock_run: MagicMock,
) -> None:
    proc = _mock_proc(returncode=-9)
    # wait() raises TimeoutExpired the first call (timeout), returns on the
    # second call (after kill).
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=1), None]
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), "/tmp/x", "prompt-abc", timeout=1)

    proc.kill.assert_called_once()
    body = mock_run.call_args.kwargs["input"]
    assert "timed out" in body
    store.emit.assert_called_once_with(
        "dispatch_failed", exit_code=-9, prompt_file="prompt-abc.md", timed_out=True
    )


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_passes_dispatch_ctx_to_watcher(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    """dispatch_to_opencode must always start the completion watcher thread so a
    non-zero exit posts a failure comment even when event_store is None."""
    mock_proc = MagicMock()
    mock_proc.pid = 5
    mock_popen.return_value = mock_proc
    settings = _test_settings(prompt_script=tmp_path / "prompt.ps1")

    dispatch_to_opencode(settings, "p", event_store=None, dispatch_ctx=_ctx())

    # Two streaming threads + one completion watcher = 3 total.
    assert mock_thread.call_count == 3


# ── Run-completion tracing: zero-work detection ────────────────────────────

# Mirrors the gap-miner-v2-oscar37 client stream: only planning/reading tools,
# no bash/task/write/edit. This is the narrate-and-self-terminate signature.
_OSCAR37_STDERR = (
    '⚙ memory-graph_search_nodes {"query":"orchestrator"}\n'
    '⚙ sequential-thinking_sequentialthinking {"thought":"planning"}\n'
    "% WebFetch https://raw.githubusercontent.com/x/y/main/z.md\n"
    "→ Read /workspace/x/local_ai_instruction_modules/ai-workflow-assignments.md\n"
)


def test_extract_tool_names_parses_glyph_lines() -> None:
    tools = extract_tool_names(_OSCAR37_STDERR)
    assert tools == {
        "memory-graph_search_nodes",
        "sequential-thinking_sequentialthinking",
        "webfetch",
        "read",
    }


def test_extract_tool_names_ignores_json_lines() -> None:
    # JSON/log lines must not false-match as tool calls.
    noise = (
        '{"entities": []}\n'
        '["a", "b"]\n'
        '"key": value\n'
        "service=bus type=message.part.delta data\n"
    )
    assert extract_tool_names(noise) == set()


def test_is_planning_tool_classifies_correctly() -> None:
    assert _is_planning_tool("memory-graph_search_nodes")
    assert _is_planning_tool("sequential-thinking_sequentialthinking")
    assert _is_planning_tool("webfetch")
    assert _is_planning_tool("read")
    # Execution / delegation tools are NOT planning tools.
    assert not _is_planning_tool("task")
    assert not _is_planning_tool("bash")
    assert not _is_planning_tool("write")
    assert not _is_planning_tool("edit")


@patch("webhook_receiver.runner.subprocess.run")
def test_zero_work_comment_posted_on_planning_only_clean_exit(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / "p.stderr").write_text(_OSCAR37_STDERR, encoding="utf-8")
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "p")

    assert mock_run.called
    body = mock_run.call_args.kwargs["input"]
    assert "no work tools" in body
    assert "memory-graph_search_nodes" in body
    store.emit.assert_called_once()
    assert store.emit.call_args.args[0] == "dispatch_zero_work"


@patch("webhook_receiver.runner.subprocess.run")
def test_no_zero_work_comment_when_execution_tool_used(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    mock_run.return_value = _closed_state_run()
    stderr = _OSCAR37_STDERR + "→ Task developer Implement the assignment\n"
    (tmp_path / "p.stderr").write_text(stderr, encoding="utf-8")
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "p")

    assert _no_comment_posted(mock_run)  # no advisory comment — it did real work
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="p.md"
    )


@patch("webhook_receiver.runner.subprocess.run")
def test_no_zero_work_analysis_when_stderr_missing(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    mock_run.return_value = _closed_state_run()
    # No stderr file -> cannot classify -> no zero-work comment (regression guard
    # for the existing zero-exit "no comment" contract).
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "missing")

    assert _no_comment_posted(mock_run)
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="missing.md"
    )


# ── Secret sanitization ────────────────────────────────────────────────────


class TestSanitizeForComment:
    # Construct fake tokens dynamically so the literal pattern doesn't appear
    # in the source file (avoids tripping the pre-commit secret scanner).
    _FAKE_GHP = "ghp_" + "A" * 36
    _FAKE_SK = "sk-" + "B" * 24
    _FAKE_FG_PAT = "github_pat_" + "C" * 22

    def test_github_pat_redacted(self) -> None:
        msg = f"Error: auth failed with {self._FAKE_GHP}"
        sanitized = _sanitize_for_comment(msg)
        assert "ghp_" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_github_fine_grained_pat_redacted(self) -> None:
        msg = f"Error: auth failed with {self._FAKE_FG_PAT}"
        sanitized = _sanitize_for_comment(msg)
        assert "github_pat_" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_openai_key_redacted(self) -> None:
        msg = f"API key {self._FAKE_SK} is invalid"
        sanitized = _sanitize_for_comment(msg)
        assert "sk-" + "B" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_bearer_token_redacted(self) -> None:
        msg = "Bearer ya29.abcdef1234567890abcdef1234567890 expired"
        sanitized = _sanitize_for_comment(msg)
        assert "ya29." not in sanitized
        assert "[REDACTED]" in sanitized

    def test_key_value_assignment_redacted(self) -> None:
        msg = "Config: api_key=sk_test_12345 not found"
        sanitized = _sanitize_for_comment(msg)
        assert "sk_test_12345" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_normal_text_preserved(self) -> None:
        msg = "level=ERROR AI_APICallError: Usage limit reached for 5 hour"
        assert _sanitize_for_comment(msg) == msg

    def test_empty_string(self) -> None:
        assert _sanitize_for_comment("") == ""


class TestBuildFailureBody:
    """Direct unit tests for the kill-reason → failure-comment mapping."""

    def test_permission_deadlock_failure_body(self) -> None:
        body = _build_failure_body(
            _ctx(),
            exit_code=-15,
            log_dir="runs/abc",
            prompt_stem="runs/abc/prompt",
            kill_reason=REASON_PERMISSION_DEADLOCK,
        )
        assert "permission ask deadlock" in body
        assert "exited with status" not in body

    def test_consecutive_errors_failure_body(self) -> None:
        body = _build_failure_body(
            _ctx(),
            exit_code=-15,
            log_dir="runs/abc",
            prompt_stem="runs/abc/prompt",
            kill_reason=REASON_CONSECUTIVE_ERRORS,
            consecutive_errors=5,
        )
        assert "consecutive errors" in body

    def test_plain_nonzero_exit_uses_status(self) -> None:
        body = _build_failure_body(
            _ctx(),
            exit_code=1,
            log_dir="runs/abc",
            prompt_stem="runs/abc/prompt",
        )
        assert "exited with status 1" in body


@patch("webhook_receiver.runner.subprocess.run")
def test_consecutive_errors_comment_is_sanitized(
    mock_run: MagicMock,
) -> None:
    """Error messages with secrets must be sanitized before posting to GitHub."""
    fake_ghp = "ghp_" + "A" * 36
    proc = _wd_proc()
    state = WatchdogState(0.0)
    for _ in range(4):
        state.record_line("level=ERROR some error")
    # The LAST error line contains the secret — this is what gets posted.
    state.record_line(f"level=ERROR auth failed token={fake_ghp}")
    cfg = WatchdogConfig(
        idle_timeout_secs=999999,
        hard_ceiling_secs=None,
        poll_interval_secs=0,
        max_consecutive_errors=5,
        error_grace_secs=300,
    )
    _run_completion_watcher(
        proc, MagicMock(), _ctx(), "/tmp/x", "prompt-abc",
        state=state, watchdog_config=cfg,
    )

    body = mock_run.call_args.kwargs["input"]
    assert "ghp_" not in body
    assert "[REDACTED]" in body


# ── Dispatch identity: workflow parse, slug, manifest ──────────────────────


def test_parse_workflow_name_extracts_dispatch_body() -> None:
    prompt = (
        "/orchestrate-dynamic-workflow\n"
        '$workflow_name = project-setup\n'
        "some trailing context"
    )
    assert _parse_workflow_name(prompt) == "project-setup"


def test_parse_workflow_name_returns_none_when_absent() -> None:
    assert _parse_workflow_name("just a regular prompt") is None
    assert _parse_workflow_name("") is None


def test_dispatch_slug_encodes_identity() -> None:
    ctx = DispatchContext(repo_full_name="owner/repo", issue_number=7)
    slug = _dispatch_slug(ctx, "project-setup", "20260704T204631Z")
    assert slug == "prompt-owner__repo__issue-7__project-setup__20260704T204631Z"


def test_dispatch_slug_without_context() -> None:
    slug = _dispatch_slug(None, None, "20260704T204631Z")
    assert slug == "prompt-adhoc__no-issue__adhoc__20260704T204631Z"


def test_write_and_update_run_manifest(tmp_path: Path) -> None:
    stem = "prompt-owner__repo__issue-7__project-setup__ts-abc"
    _write_run_manifest(
        tmp_path,
        stem,
        {"stem": stem, "workflow": "project-setup", "started_at": "ts"},
    )
    _update_run_manifest(
        tmp_path,
        stem,
        {"exit_code": 0, "classification": "completed"},
    )
    import json

    data = json.loads((tmp_path / f"{stem}.manifest.json").read_text())
    assert data["workflow"] == "project-setup"  # start fields preserved
    assert data["exit_code"] == 0  # completion fields merged
    assert data["classification"] == "completed"


# ── Incomplete-run detection ───────────────────────────────────────────────


def test_dispatch_issue_closed_true_when_closed() -> None:
    with patch("webhook_receiver.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"state":"closed"}'
        )
        assert _dispatch_issue_closed(_ctx()) is True


def test_dispatch_issue_closed_false_when_open() -> None:
    with patch("webhook_receiver.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='{"state":"open"}'
        )
        assert _dispatch_issue_closed(_ctx()) is False


def test_dispatch_issue_closed_true_on_gh_error() -> None:
    """A failing/non-JSON gh response must never false-positive 'incomplete'."""
    with patch("webhook_receiver.runner.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=""
        )
        assert _dispatch_issue_closed(_ctx()) is True


@patch("webhook_receiver.runner.subprocess.run")
def test_incomplete_comment_posted_when_issue_still_open(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    # Real work tool used (so not zero-work) but the dispatch issue is OPEN.
    # `gh issue view` (state check) returns open; the subsequent comment post
    # is a different call and must not exhaust the side effect.
    def _run_side_effect(*args, **kwargs):
        cmd = args[0] if args else None
        if cmd and "view" in cmd:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"state":"open"}'
            )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

    mock_run.side_effect = _run_side_effect
    stderr = _OSCAR37_STDERR + "→ Task developer Do the work\n"
    (tmp_path / "p.stderr").write_text(stderr, encoding="utf-8")
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "p")

    # An incomplete advisory comment is posted...
    bodies = [c.kwargs["input"] for c in mock_run.call_args_list if "input" in c.kwargs]
    assert any("dispatch issue is still open" in b for b in bodies)
    # ...and the run is classified incomplete (not completed).
    store.emit.assert_called_once()
    assert store.emit.call_args.args[0] == "dispatch_incomplete"


@patch("webhook_receiver.runner.subprocess.run")
def test_incomplete_manifest_recorded(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"state":"open"}'
    )
    (tmp_path / "p.stderr").write_text("→ Task developer work\n", encoding="utf-8")
    proc = _mock_proc(returncode=0)
    _run_completion_watcher(proc, MagicMock(), _ctx(), str(tmp_path), "p")

    import json

    mf = json.loads((tmp_path / "p.manifest.json").read_text())
    assert mf["classification"] == "incomplete"
    assert mf["exit_code"] == 0


@patch("webhook_receiver.runner.subprocess.run")
def test_incomplete_not_triggered_for_non_dispatch_label(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Only ``orchestration:dispatch`` carries the close-on-success contract.

    A ``plan-approved``/epic dispatch that succeeds leaves the triggering issue
    open by design (the clause creates an epic and skips to Final). It must NOT
    be flagged incomplete — the label gate exists to prevent that false positive.
    """
    mock_run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"state":"open"}'
    )
    (tmp_path / "p.stderr").write_text("→ Task developer work\n", encoding="utf-8")
    proc = _mock_proc(returncode=0)
    ctx = DispatchContext(
        repo_full_name="owner/repo",
        issue_number=7,
        trigger_label="orchestration:plan-approved",
    )
    store = MagicMock()
    _run_completion_watcher(proc, store, ctx, str(tmp_path), "p")

    # No advisory comment posted …
    bodies = [c.kwargs["input"] for c in mock_run.call_args_list if "input" in c.kwargs]
    assert not any("dispatch issue is still open" in b for b in bodies)
    # … and the run is classified completed, not incomplete.
    store.emit.assert_called_once()
    assert store.emit.call_args.args[0] == "dispatch_completed"


@patch("webhook_receiver.runner.subprocess.run")
def test_incomplete_triggered_for_direct_body_label(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """``gh-issue-tracking:direct-body`` carries the same close-on-success
    contract as ``orchestration:dispatch``: a clean, real-work run that leaves
    the triggering issue open is flagged incomplete (the silent false-success
    mode the check was added to catch).
    """
    def _run_side_effect(*args, **kwargs):
        cmd = args[0] if args else None
        if cmd and "view" in cmd:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"state":"open"}'
            )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="")

    mock_run.side_effect = _run_side_effect
    (tmp_path / "p.stderr").write_text("→ Task developer work\n", encoding="utf-8")
    proc = _mock_proc(returncode=0)
    ctx = DispatchContext(
        repo_full_name="owner/repo",
        issue_number=7,
        trigger_label="gh-issue-tracking:direct-body",
    )
    store = MagicMock()

    _run_completion_watcher(proc, store, ctx, str(tmp_path), "p")

    # An incomplete advisory comment is posted …
    bodies = [c.kwargs["input"] for c in mock_run.call_args_list if "input" in c.kwargs]
    assert any("dispatch issue is still open" in b for b in bodies)
    # … and the run is classified incomplete (not completed).
    store.emit.assert_called_once()
    assert store.emit.call_args.args[0] == "dispatch_incomplete"


# ── dispatch_to_opencode writes identity manifest + slug filename ───────────


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_writes_manifest_and_uses_slug_prefix(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    import json

    mock_proc = MagicMock()
    mock_proc.pid = 4242
    mock_popen.return_value = mock_proc
    settings = _test_settings(log_dir=tmp_path)
    prompt = (
        "/orchestrate-dynamic-workflow\n$workflow_name = project-setup\n"
    )

    dispatch_to_opencode(settings, prompt, dispatch_ctx=_ctx())

    manifests = list(tmp_path.glob("*.manifest.json"))
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text())
    assert data["workflow"] == "project-setup"
    assert data["repo_full_name"] == "owner/repo"
    assert data["issue_number"] == 7
    assert data["pid"] == 4242
    # Slug prefix encodes repo/issue/workflow; keeps the prompt- glob working.
    stem = manifests[0].name[: -len(".manifest.json")]
    assert stem.startswith("prompt-owner__repo__issue-7__project-setup__")
    assert (tmp_path / f"{stem}.md").exists()


def test_dispatch_slug_dotted_workflow_and_repo_pass_stem_validator() -> None:
    """Producer/validator invariant: a dotted workflow OR repo name must produce
    a stem the dashboard accepts (no HTTP 400 on the detail/logs routes)."""
    from webhook_receiver.dashboard import _valid_run_stem

    ctx = DispatchContext(repo_full_name="owner/repo.name", issue_number=1)
    stem = _dispatch_slug(ctx, "my.workflow", "20260704T204631Z")
    assert _valid_run_stem(stem), f"stem failed validator: {stem!r}"
    assert "." not in stem


# ── Watchdog integration with _run_completion_watcher ──────────────────────


def _wd_proc(returncode_none_first: bool = True) -> MagicMock:
    """Mock proc for watchdog-path testing.

    *returncode_none_first*: when True, poll() returns None first (process
    running) then the returncode (process killed/exited). This simulates the
    watchdog seeing a live process then killing it.
    """
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = -15
    if returncode_none_first:
        proc.poll.side_effect = [None, -15]
    else:
        proc.poll.return_value = -15
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = MagicMock()
    return proc


def _idle_state(idle_secs: float = 1000) -> WatchdogState:
    """A WatchdogState whose last_line_time is *idle_secs* in the past."""
    import time as _time

    state = WatchdogState(_time.monotonic() - idle_secs)
    state._last_line_time = _time.monotonic() - idle_secs
    return state


@patch("webhook_receiver.runner.subprocess.run")
def test_watchdog_idle_timeout_posts_failure_comment(
    mock_run: MagicMock,
) -> None:
    """When the watchdog kills on idle timeout, _run_completion_watcher posts
    a failure comment with the idle-timeout reason (not generic 'timed out')."""
    proc = _wd_proc()
    state = _idle_state(idle_secs=1000)
    cfg = WatchdogConfig(
        idle_timeout_secs=1,
        hard_ceiling_secs=None,
        poll_interval_secs=0,
    )
    store = MagicMock()
    _run_completion_watcher(
        proc, store, _ctx(), "/tmp/x", "prompt-abc",
        state=state, watchdog_config=cfg,
    )

    body = mock_run.call_args.kwargs["input"]
    assert "idle" in body.lower()
    store.emit.assert_called_once()
    assert store.emit.call_args.kwargs.get("timed_out") is True


@patch("webhook_receiver.runner.subprocess.run")
def test_watchdog_hard_ceiling_posts_failure_comment(
    mock_run: MagicMock,
) -> None:
    """When the watchdog kills on hard ceiling, the failure comment says so."""
    proc = _wd_proc()
    # Active state (recent lines) but elapsed exceeds ceiling.
    import time as _time

    state = WatchdogState(_time.monotonic() - 100)
    state.record_line("recent activity")
    cfg = WatchdogConfig(
        idle_timeout_secs=999999,
        hard_ceiling_secs=1,
        poll_interval_secs=0,
    )
    store = MagicMock()
    _run_completion_watcher(
        proc, store, _ctx(), "/tmp/x", "prompt-abc",
        state=state, watchdog_config=cfg,
    )

    body = mock_run.call_args.kwargs["input"]
    assert "ceiling" in body.lower()
    assert "idle" not in body.lower()


@patch("webhook_receiver.runner.subprocess.run")
def test_watchdog_consecutive_errors_posts_failure_comment(
    mock_run: MagicMock,
) -> None:
    """When the watchdog kills on consecutive errors, the failure comment
    includes the error count and last error message."""
    proc = _wd_proc()
    state = WatchdogState(0.0)
    for i in range(5):
        state.record_line(f"level=ERROR AI_APICallError: Usage limit {i}")
    cfg = WatchdogConfig(
        idle_timeout_secs=999999,
        hard_ceiling_secs=None,
        poll_interval_secs=0,
        max_consecutive_errors=5,
        error_grace_secs=300,
    )
    store = MagicMock()
    _run_completion_watcher(
        proc, store, _ctx(), "/tmp/x", "prompt-abc",
        state=state, watchdog_config=cfg,
    )

    body = mock_run.call_args.kwargs["input"]
    assert "consecutive" in body.lower()
    assert "Usage limit" in body


@patch("webhook_receiver.runner.subprocess.run")
def test_watchdog_classification_in_manifest(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """The manifest records the specific kill reason as the classification."""
    import json

    proc = _wd_proc()
    state = _idle_state(idle_secs=1000)
    cfg = WatchdogConfig(
        idle_timeout_secs=1,
        hard_ceiling_secs=None,
        poll_interval_secs=0,
    )
    _run_completion_watcher(
        proc, MagicMock(), _ctx(), str(tmp_path), "p",
        state=state, watchdog_config=cfg,
    )

    mf = json.loads((tmp_path / "p.manifest.json").read_text())
    assert mf["classification"] == REASON_IDLE_TIMEOUT
    assert mf["kill_reason"] == REASON_IDLE_TIMEOUT


@patch("webhook_receiver.runner.subprocess.run")
def test_watchdog_process_exit_no_kill_reason(
    mock_run: MagicMock,
) -> None:
    """When the process exits on its own via the watchdog path, no kill reason
    is set and the standard exit-code classification applies."""
    mock_run.return_value = _closed_state_run()
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = 0
    proc.poll.return_value = 0  # process exited cleanly on first check
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = MagicMock()
    state = WatchdogState(0.0)
    cfg = WatchdogConfig(poll_interval_secs=0)
    store = MagicMock()
    _run_completion_watcher(
        proc, store, _ctx(), "/tmp/x", "prompt-abc",
        state=state, watchdog_config=cfg,
    )

    assert _no_comment_posted(mock_run)
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="prompt-abc.md"
    )
