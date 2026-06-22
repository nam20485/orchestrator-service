"""Stage 3 integration: BeadsLoop poll → spawn → close cycle.

Tests the full beads execution path with real prompt building (``_build_bead_prompt``)
and mocked subprocess for ``br``/``bvr``/agent execution. Exercises retry logic,
workspace management, and the inter-stage boundaries (DAG → prompt → agent → close).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings


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
        beads_enabled=True,
        beads_poll_interval=1,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
        beads_target_repo="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


def _bead(bead_id: str = "br-1", title: str = "Task", priority: int = 1) -> dict:
    return {"id": bead_id, "title": title, "priority": priority, "description": "Do work"}


# ── Stage 3: poll → close happy path ───────────────────────────────────────


@patch("webhook_receiver.beads_loop.threading.Thread")
@patch("webhook_receiver.beads_loop.subprocess.Popen")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_poll_to_close_happy_path(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_popen: MagicMock,
    mock_thread: MagicMock,
) -> None:
    """Full poll → process → spawn agent → verify close."""
    mock_proc = MagicMock()
    mock_proc.wait.return_value = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc

    # br ready returns a bead, bvr --robot-next also returns it
    bead = _bead()
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-1"})),  # bvr --robot-next
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    loop = BeadsLoop(_test_settings())
    loop._poll_and_process()

    mock_popen.assert_called_once()
    assert "br-1" not in loop._active_beads


@patch("webhook_receiver.beads_loop.threading.Thread")
@patch("webhook_receiver.beads_loop.subprocess.Popen")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="open")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_retry_on_agent_failure(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_popen: MagicMock,
    mock_thread: MagicMock,
) -> None:
    """Agent fails (bead still open) → retry state incremented."""
    mock_proc = MagicMock()
    mock_proc.wait.return_value = 1
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc

    bead = _bead("br-fail")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-fail"})),  # bvr
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    loop = BeadsLoop(_test_settings())
    loop._poll_and_process()

    assert loop._retry_state["br-fail"]["count"] == 1


@patch("webhook_receiver.beads_loop.create_workspace")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_workspace_creation_failure(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_ws: MagicMock,
) -> None:
    """Workspace clone fails → retry incremented, no agent spawned."""
    mock_create_ws.side_effect = Exception("clone failed")
    bead = _bead("br-wsfail")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-wsfail"})),  # bvr
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    loop = BeadsLoop(_test_settings(beads_target_repo="https://github.com/o/r.git"))
    loop._poll_and_process()

    mock_spawn.assert_not_called()
    assert loop._retry_state["br-wsfail"]["count"] == 1


@patch("webhook_receiver.beads_loop.create_workspace")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent", return_value=(True, ""))
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_push_failure_still_clears_retry(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_ws: MagicMock,
) -> None:
    """Agent succeeds, push fails → retry state cleared (bead was closed)."""
    mock_create_ws.return_value = "/workspace/br-push"
    bead = _bead("br-push")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-push"})),
        _mock_completed(json.dumps({"issues": [bead]})),
    ]

    loop = BeadsLoop(_test_settings(beads_target_repo="https://github.com/o/r.git"))
    with (
        patch("webhook_receiver.beads_loop.push_branch", side_effect=Exception("push fail")),
        patch("webhook_receiver.beads_loop.create_pr"),
        patch("webhook_receiver.beads_loop.cleanup_workspace"),
    ):
        loop._poll_and_process()

    assert "br-push" not in loop._retry_state


# ── Stage 3: concurrent bead locking ──────────────────────────────────────


def test_beads_loop_concurrent_beads_lock() -> None:
    """Two beads ready, first active → second skipped."""
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("br-active")
    with (
        patch.object(loop, "_get_next_bead", return_value={"id": "br-active"}),
        patch.object(loop, "_process_bead") as mock_process,
    ):
        loop._poll_and_process()
        mock_process.assert_not_called()


# ── Stage 3: retry logic deep tests ────────────────────────────────────────


def test_beads_loop_injects_previous_logs_on_retry() -> None:
    """Second attempt prompt contains error context from first failure."""
    loop = BeadsLoop(_test_settings())
    loop._retry_state["br-ctx"] = {"count": 1, "logs": "ERROR: build failed"}
    bead = {"id": "br-ctx", "title": "T", "description": "Do work"}
    prompt = loop._build_bead_prompt(bead, 1, previous_logs="ERROR: build failed")

    assert "WARNING" in prompt
    assert "ERROR: build failed" in prompt
    assert "br close br-ctx" in prompt


def test_beads_loop_halt_after_max_retries() -> None:
    """Exhausted retries → no further spawn, bead left open."""
    settings = _test_settings(beads_max_retries=2)
    loop = BeadsLoop(settings)
    loop._retry_state["br-max"] = {"count": 2, "logs": "error"}
    bead = {"id": "br-max", "title": "T", "priority": 1}

    with patch.object(loop, "_spawn_agent") as mock_spawn:
        loop._process_bead(bead)
        mock_spawn.assert_not_called()


@patch("webhook_receiver.beads_loop.create_workspace")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent", return_value=(True, ""))
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_clears_retry_state_on_success(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_ws: MagicMock,
) -> None:
    """Succeed after a prior retry → retry state removed."""
    mock_create_ws.return_value = "/workspace/br-clear"
    bead = _bead("br-clear")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-clear"})),
        _mock_completed(json.dumps({"issues": [bead]})),
    ]

    loop = BeadsLoop(_test_settings(beads_target_repo="https://github.com/o/r.git"))
    loop._retry_state["br-clear"] = {"count": 1, "logs": "old error"}
    with (
        patch("webhook_receiver.beads_loop.push_branch"),
        patch("webhook_receiver.beads_loop.create_pr"),
        patch("webhook_receiver.beads_loop.cleanup_workspace"),
    ):
        loop._poll_and_process()

    assert "br-clear" not in loop._retry_state
