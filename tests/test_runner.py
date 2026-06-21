from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver.config import Settings
from webhook_receiver.runner import (
    _base_args,
    _prompt_script_invocation,
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
        allowed_events=None,
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=False,
        beads_poll_interval=10,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        beads_target_repo="",
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

    assert mock_thread.call_count == 2
    targets = [c.kwargs.get("target") or c.args[0] for c in mock_thread.call_args_list]
    assert all(t is _stream_to_logger_and_file for t in targets)
