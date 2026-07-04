from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver.config import Settings
from webhook_receiver.runner import (
    DispatchContext,
    _base_args,
    _extract_tool_invocations,
    _is_planning_tool,
    _prompt_script_invocation,
    _run_completion_watcher,
    _stream_to_logger_and_file,
    dispatch_to_opencode,
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
    return DispatchContext(repo_full_name="owner/repo", issue_number=7)


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
    proc = _mock_proc(returncode=0)
    store = MagicMock()
    _run_completion_watcher(proc, store, _ctx(), "/tmp/x", "prompt-abc")

    assert not mock_run.called
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


def test_extract_tool_invocations_parses_glyph_lines() -> None:
    tools = _extract_tool_invocations(_OSCAR37_STDERR)
    assert tools == {
        "memory-graph_search_nodes",
        "sequential-thinking_sequentialthinking",
        "webfetch",
        "read",
    }


def test_extract_tool_invocations_ignores_json_lines() -> None:
    # JSON/log lines must not false-match as tool calls.
    noise = (
        '{"entities": []}\n'
        '["a", "b"]\n'
        '"key": value\n'
        "service=bus type=message.part.delta data\n"
    )
    assert _extract_tool_invocations(noise) == set()


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
    stderr = _OSCAR37_STDERR + "→ Task developer Implement the assignment\n"
    (tmp_path / "p.stderr").write_text(stderr, encoding="utf-8")
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "p")

    assert not mock_run.called  # no advisory comment — it did real work
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="p.md"
    )


@patch("webhook_receiver.runner.subprocess.run")
def test_no_zero_work_analysis_when_stderr_missing(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    # No stderr file -> cannot classify -> no zero-work comment (regression guard
    # for the existing zero-exit "no comment" contract).
    proc = _mock_proc(returncode=0)
    store = MagicMock()

    _run_completion_watcher(proc, store, _ctx(), str(tmp_path), "missing")

    assert not mock_run.called
    store.emit.assert_called_once_with(
        "dispatch_completed", exit_code=0, prompt_file="missing.md"
    )
