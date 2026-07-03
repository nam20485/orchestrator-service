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
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=True,
        beads_poll_interval=1,
        beads_max_retries=3,
        beads_workspace_root="/workspace",
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


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.threading.Thread")
@patch("webhook_receiver.beads_loop.subprocess.Popen")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_poll_to_close_happy_path(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_popen: MagicMock,
    mock_thread: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    """Full poll → process → spawn agent → verify close."""
    mock_proc = MagicMock()
    mock_proc.wait.return_value = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-1"

    # br ready returns a bead, bvr --robot-next also returns it
    bead = _bead()
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-1"})),  # bvr --robot-next
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    settings = _test_settings()
    loop = BeadsLoop(settings)
    # Simulate a discovered project so the scan picks it up
    with patch(
        "webhook_receiver.beads_loop.discover_projects", return_value=["proj"]
    ), patch(
        "webhook_receiver.beads_loop.project_workspace_path",
        return_value="/workspace/proj",
    ):
        loop._scan_and_process()

    mock_popen.assert_called_once()
    assert "br-1" not in loop._active_beads


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.threading.Thread")
@patch("webhook_receiver.beads_loop.subprocess.Popen")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="open")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_retry_on_agent_failure(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_popen: MagicMock,
    mock_thread: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    """Agent fails (bead still open) → retry state incremented."""
    mock_proc = MagicMock()
    mock_proc.wait.return_value = 1
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-fail"

    bead = _bead("br-fail")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-fail"})),  # bvr
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    settings = _test_settings()
    loop = BeadsLoop(settings)
    with patch(
        "webhook_receiver.beads_loop.discover_projects", return_value=["proj"]
    ), patch(
        "webhook_receiver.beads_loop.project_workspace_path",
        return_value="/workspace/proj",
    ):
        loop._scan_and_process()

    assert loop._retry_state["proj:br-fail"]["count"] == 1


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_worktree_creation_failure(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    """Worktree creation fails → retry incremented, no agent spawned."""
    mock_create_wt.side_effect = Exception("worktree failed")
    bead = _bead("br-wsfail")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-wsfail"})),  # bvr
        _mock_completed(json.dumps({"issues": [bead]})),  # br ready
    ]

    settings = _test_settings()
    loop = BeadsLoop(settings)
    with patch(
        "webhook_receiver.beads_loop.discover_projects", return_value=["proj"]
    ), patch(
        "webhook_receiver.beads_loop.project_workspace_path",
        return_value="/workspace/proj",
    ):
        loop._scan_and_process()

    mock_spawn.assert_not_called()
    assert loop._retry_state["proj:br-wsfail"]["count"] == 1


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent", return_value=(True, ""))
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_push_failure_still_clears_retry(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    """Agent succeeds, push fails → retry state cleared (bead was closed)."""
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-push"
    bead = _bead("br-push")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-push"})),
        _mock_completed(json.dumps({"issues": [bead]})),
    ]

    settings = _test_settings()
    loop = BeadsLoop(settings)
    with patch(
        "webhook_receiver.beads_loop.discover_projects", return_value=["proj"]
    ), patch(
        "webhook_receiver.beads_loop.project_workspace_path",
        return_value="/workspace/proj",
    ):
        with (
            patch("webhook_receiver.beads_loop.push_branch", side_effect=Exception("push fail")),
            patch("webhook_receiver.beads_loop.create_pr"),
        ):
            loop._scan_and_process()

    assert "br-push" not in loop._retry_state


# ── Stage 3: concurrent bead locking ──────────────────────────────────────


def test_beads_loop_concurrent_beads_lock() -> None:
    """Two beads ready, first active → second skipped."""
    loop = BeadsLoop(_test_settings())
    loop._active_beads.add("proj:br-active")
    with (
        patch.object(loop, "_get_next_bead", return_value={"id": "br-active"}),
        patch.object(loop, "_process_bead") as mock_process,
    ):
        loop._poll_and_process_project("proj", "/workspace/proj")
        mock_process.assert_not_called()


# ── Stage 3: retry logic deep tests ────────────────────────────────────────


def test_beads_loop_injects_previous_logs_on_retry(tmp_path: Path) -> None:
    """Second attempt prompt contains error context from first failure."""
    loop = BeadsLoop(_test_settings())
    loop._retry_state["proj:br-ctx"] = {"count": 1, "logs": "ERROR: build failed"}
    bead = {"id": "br-ctx", "title": "T", "description": "Do work"}
    with patch("webhook_receiver.beads_loop.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        prompt = loop._build_bead_prompt(
            bead, 1, str(tmp_path), "/workspace/proj",
            previous_logs="ERROR: build failed"
        )

    assert "WARNING" in prompt
    assert "ERROR: build failed" in prompt
    assert "br close br-ctx" in prompt


def test_beads_loop_halt_after_max_retries() -> None:
    """Exhausted retries → no further spawn, bead left open."""
    settings = _test_settings(beads_max_retries=2)
    loop = BeadsLoop(settings)
    loop._retry_state["proj:br-max"] = {"count": 2, "logs": "error"}
    bead = {"id": "br-max", "title": "T", "priority": 1}

    with patch.object(loop, "_spawn_agent") as mock_spawn:
        loop._process_bead(bead, "proj", "/workspace/proj")
        mock_spawn.assert_not_called()


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent", return_value=(True, ""))
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status", return_value="closed")
@patch("webhook_receiver.beads_loop.subprocess.run")
def test_beads_loop_clears_retry_state_on_success(
    mock_run: MagicMock,
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    """Succeed after a prior retry → retry state removed."""
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-clear"
    bead = _bead("br-clear")
    mock_run.side_effect = [
        _mock_completed(json.dumps({"id": "br-clear"})),
        _mock_completed(json.dumps({"issues": [bead]})),
    ]

    settings = _test_settings()
    loop = BeadsLoop(settings)
    loop._retry_state["proj:br-clear"] = {"count": 1, "logs": "old error"}
    with patch(
        "webhook_receiver.beads_loop.discover_projects", return_value=["proj"]
    ), patch(
        "webhook_receiver.beads_loop.project_workspace_path",
        return_value="/workspace/proj",
    ):
        with (
            patch("webhook_receiver.beads_loop.push_branch"),
            patch("webhook_receiver.beads_loop.create_pr"),
        ):
            loop._scan_and_process()

    assert "proj:br-clear" not in loop._retry_state
