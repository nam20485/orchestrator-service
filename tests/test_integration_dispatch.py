"""Stage 2 integration: prompt → dispatch → subprocess spawn.

Tests the full dispatch path at the inter-stage boundary between prompt assembly
and subprocess execution. Uses mocked Popen to verify command construction,
temp file creation, and streaming thread launch without requiring pwsh.
"""
from __future__ import annotations

import glob
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from webhook_receiver.config import Settings
from webhook_receiver.runner import dispatch_to_opencode


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


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_end_to_end_creates_files_and_spawns(
    mock_popen: MagicMock, mock_thread: MagicMock, tmp_path: Path
) -> None:
    """Full dispatch: temp file written, subprocess spawned, threads started."""
    mock_proc = MagicMock()
    mock_proc.pid = 777
    mock_popen.return_value = mock_proc

    settings = _test_settings(prompt_script=tmp_path / "prompt.ps1")
    prompt = "You are assigned bead br-xyz. Do work. Run br close br-xyz."

    dispatch_to_opencode(settings, prompt)

    assert mock_popen.called
    assert mock_thread.call_count == 2

    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "pwsh"
    assert "-PromptFile" in cmd


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_prompt_file_contains_full_prompt(
    mock_popen: MagicMock, mock_thread: MagicMock
) -> None:
    """The temp prompt file must contain the exact prompt string passed."""
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_popen.return_value = mock_proc

    marker = "# UNIQUE-BEAD-br-test-9182\n\nImplement feature X."
    settings = _test_settings()
    dispatch_to_opencode(settings, marker)

    log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
    prompt_files = sorted(glob.glob(str(log_dir / "prompt-*.md")))
    matching = [f for f in prompt_files if Path(f).read_text() == marker]
    assert len(matching) >= 1


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_concurrent_dispatches_use_unique_files(
    mock_popen: MagicMock, mock_thread: MagicMock
) -> None:
    """Two rapid dispatches must create distinct prompt files."""
    mock_proc = MagicMock()
    mock_proc.pid = 1
    mock_popen.return_value = mock_proc

    settings = _test_settings()
    dispatch_to_opencode(settings, "UNIQUE-MARKER-AAA-7741")
    dispatch_to_opencode(settings, "UNIQUE-MARKER-BBB-7741")

    log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
    prompt_files = sorted(glob.glob(str(log_dir / "prompt-*.md")))
    contents = {Path(f).read_text() for f in prompt_files}
    assert "UNIQUE-MARKER-AAA-7741" in contents
    assert "UNIQUE-MARKER-BBB-7741" in contents


# ── Stage 2: failure handling ──────────────────────────────────────────────


@patch("webhook_receiver.runner.threading.Thread")
@patch("webhook_receiver.runner.subprocess.Popen")
def test_dispatch_handles_subprocess_crash(
    mock_popen: MagicMock, mock_thread: MagicMock
) -> None:
    """Popen raising OSError should propagate (caller handles in background task)."""
    mock_popen.side_effect = OSError("command not found")
    settings = _test_settings()
    import pytest

    with pytest.raises(OSError):
        dispatch_to_opencode(settings, "prompt")
