from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver.beads_loop import BeadsLoop, _extract_bead
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
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_result(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


@pytest.fixture
def mock_empty_beads() -> Any:
    """Patch ``subprocess.run`` so ``_run_beads_cmd`` does not shell out.

    Returns empty stdout for every call (beads lookup + graph), so the progress
    snapshot degrades to ``0/0 beads closed`` without spawning real processes.
    """
    with patch("webhook_receiver.beads_loop.subprocess.run") as mock_run:
        mock_run.return_value = _mock_result("")
        yield mock_run


# ── _extract_bead ─────────────────────────────────────────────────────────


def test_extract_bead_flat_id() -> None:
    assert _extract_bead({"id": "br-x", "title": "T"}) == {"id": "br-x", "title": "T"}


def test_extract_bead_nested_under_bead() -> None:
    data = {"bead": {"id": "br-a", "title": "A"}}
    assert _extract_bead(data) == {"id": "br-a", "title": "A"}


def test_extract_bead_nested_under_recommendation() -> None:
    data = {"recommendation": {"id": "br-b", "score": 0.9}}
    assert _extract_bead(data) == {"id": "br-b", "score": 0.9}


def test_extract_bead_nested_under_next() -> None:
    data = {"next": {"id": "br-c"}}
    assert _extract_bead(data)["id"] == "br-c"


def test_extract_bead_from_issues_list() -> None:
    data = {"issues": [{"id": "br-d"}]}
    assert _extract_bead(data)["id"] == "br-d"


def test_extract_bead_empty() -> None:
    assert _extract_bead({}) is None


def test_extract_bead_no_id() -> None:
    assert _extract_bead({"score": 0.9, "reason": "high centrality"}) is None


def test_extract_bead_not_dict() -> None:
    assert _extract_bead([1, 2, 3]) is None
    assert _extract_bead("string") is None
    assert _extract_bead(None) is None


# ── _build_bead_prompt ────────────────────────────────────────────────────


def test_build_prompt_basic(tmp_path: Path, mock_empty_beads: None) -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-1", "title": "Task One", "description": "Do the thing"}
    prompt = loop._build_bead_prompt(bead, 0, str(tmp_path), "/workspace")
    assert "br-1" in prompt
    assert "Task One" in prompt
    assert "Do the thing" in prompt
    assert "br close br-1" in prompt
    assert "BEADS_AGENT_GUIDE.md" in prompt
    assert "Progress:" in prompt


def test_build_prompt_retry_with_logs(tmp_path: Path, mock_empty_beads: None) -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-2", "title": "Task Two", "description": "Do work"}
    prompt = loop._build_bead_prompt(
        bead, 1, str(tmp_path), "/workspace", previous_logs="ERROR: test failed"
    )
    assert "WARNING" in prompt
    assert "ERROR: test failed" in prompt
    assert "br close br-2" in prompt


def test_build_prompt_no_description(tmp_path: Path, mock_empty_beads: None) -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-3", "title": "Task Three"}
    prompt = loop._build_bead_prompt(bead, 0, str(tmp_path), "/workspace")
    assert "br-3" in prompt
    assert "Task Three" in prompt


def test_build_prompt_no_first_attempt_no_warning(
    tmp_path: Path, mock_empty_beads: None
) -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-4", "title": "T", "description": "D"}
    prompt = loop._build_bead_prompt(
        bead, 0, str(tmp_path), "/workspace", previous_logs=""
    )
    assert "WARNING" not in prompt


def test_build_prompt_writes_context_files(tmp_path: Path, mock_empty_beads: None) -> None:
    """_build_bead_prompt writes BEADS_AGENT_GUIDE.md + AGENTS.md (bare workspace)."""
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-5", "title": "T", "description": "D"}
    loop._build_bead_prompt(bead, 0, str(tmp_path), "/workspace")
    assert (tmp_path / "BEADS_AGENT_GUIDE.md").exists()
    assert (tmp_path / "AGENTS.md").exists()


def test_build_prompt_keeps_existing_agents_md(
    tmp_path: Path, mock_empty_beads: None
) -> None:
    """An existing AGENTS.md (cloned repo) is never clobbered."""
    (tmp_path / "AGENTS.md").write_text("REPO INSTRUCTIONS", encoding="utf-8")
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-6", "title": "T", "description": "D"}
    loop._build_bead_prompt(bead, 0, str(tmp_path), "/workspace")
    assert (tmp_path / "AGENTS.md").read_text() == "REPO INSTRUCTIONS"
    assert (tmp_path / "BEADS_AGENT_GUIDE.md").exists()


def test_build_prompt_overview_from_canonical_root(
    tmp_path: Path, mock_empty_beads: None
) -> None:
    """Overview is built from the worktree (ws_path) which inherits the plan.

    A per-bead git worktree checks out the project repo, so if the plan is
    committed it is visible in the worktree.  This test verifies the guide
    sources its overview from ws_path when the plan exists there.
    """
    ws = tmp_path / "worktree"
    (ws / "plan_docs").mkdir(parents=True)
    (ws / "plan_docs" / "application_plan.md").write_text(
        "# Canonical Project Plan\n\nThe real plan lives in the worktree.",
        encoding="utf-8",
    )
    project_root = str(tmp_path / "project")

    settings = _test_settings(beads_workspace_root=project_root)
    loop = BeadsLoop(settings)
    bead = {"id": "br-7", "title": "T", "description": "D"}
    loop._build_bead_prompt(bead, 0, str(ws), project_root)

    guide = (ws / "BEADS_AGENT_GUIDE.md").read_text(encoding="utf-8")
    assert "Canonical Project Plan" in guide
    assert "No application_plan.md found" not in guide


# ── _get_next_bead_bvr ───────────────────────────────────────────────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_returns_bead(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps({"id": "br-bvr1", "title": "Graph-aware pick", "priority": 1})
    )
    loop = BeadsLoop(_test_settings())
    bead = loop._get_next_bead_bvr("/workspace/proj")
    assert bead is not None
    assert bead["id"] == "br-bvr1"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_nested_bead(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps({"bead": {"id": "br-bvr2", "title": "Nested"}})
    )
    loop = BeadsLoop(_test_settings())
    bead = loop._get_next_bead_bvr("/workspace/proj")
    assert bead is not None
    assert bead["id"] == "br-bvr2"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_not_found(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError()
    loop = BeadsLoop(_test_settings())
    assert loop._get_next_bead_bvr("/workspace/proj") is None


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_called_process_error(mock_run: MagicMock) -> None:
    mock_run.side_effect = __import__(
        "subprocess", fromlist=["CalledProcessError"]
    ).CalledProcessError(1, "bvr", stderr="error")
    loop = BeadsLoop(_test_settings())
    assert loop._get_next_bead_bvr("/workspace/proj") is None


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_empty_stdout(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("")
    loop = BeadsLoop(_test_settings())
    assert loop._get_next_bead_bvr("/workspace/proj") is None


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_invalid_json(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("not json at all")
    loop = BeadsLoop(_test_settings())
    assert loop._get_next_bead_bvr("/workspace/proj") is None


# ── _get_next_bead (combined bvr + br fallback) ───────────────────────────


def test_get_next_bead_prefers_bvr() -> None:
    loop = BeadsLoop(_test_settings())
    with (
        patch.object(loop, "_get_next_bead_bvr", return_value={"id": "br-bvr"}),
        patch.object(loop, "_get_ready_beads") as mock_ready,
    ):
        result = loop._get_next_bead("/workspace/proj")
        assert result is not None
        assert result["id"] == "br-bvr"
        mock_ready.assert_not_called()


def test_get_next_bead_falls_back_to_br() -> None:
    loop = BeadsLoop(_test_settings())
    with (
        patch.object(loop, "_get_next_bead_bvr", return_value=None),
        patch.object(loop, "_get_ready_beads", return_value=[
            {"id": "br-a", "priority": 1},
            {"id": "br-b", "priority": 2},
        ]),
    ):
        result = loop._get_next_bead("/workspace/proj")
        assert result is not None
        assert result["id"] == "br-a"


def test_get_next_bead_both_empty() -> None:
    loop = BeadsLoop(_test_settings())
    with (
        patch.object(loop, "_get_next_bead_bvr", return_value=None),
        patch.object(loop, "_get_ready_beads", return_value=[]),
    ):
        assert loop._get_next_bead("/workspace/proj") is None


# ── _log_overview_if_idle ─────────────────────────────────────────────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_log_overview_success(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result('{"open": 3, "blocked": 1}')
    loop = BeadsLoop(_test_settings())
    loop._log_overview_if_idle("/workspace/proj")


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_log_overview_bvr_unavailable(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError()
    loop = BeadsLoop(_test_settings())
    loop._log_overview_if_idle("/workspace/proj")


# ── _get_ready_beads (br fallback) ────────────────────────────────────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_parses_issues_list(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps({"issues": [{"id": "br-a", "title": "T", "priority": 1}]})
    )
    loop = BeadsLoop(_test_settings())
    beads = loop._get_ready_beads("/workspace/proj")
    assert len(beads) == 1
    assert beads[0]["id"] == "br-a"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_parses_plain_list(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps([{"id": "br-b", "title": "T2", "priority": 2}])
    )
    loop = BeadsLoop(_test_settings())
    beads = loop._get_ready_beads("/workspace/proj")
    assert len(beads) == 1
    assert beads[0]["id"] == "br-b"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_empty_stdout(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("")
    loop = BeadsLoop(_test_settings())
    assert loop._get_ready_beads("/workspace/proj") == []


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_br_not_found(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError()
    loop = BeadsLoop(_test_settings())
    assert loop._get_ready_beads("/workspace/proj") == []


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_called_process_error(mock_run: MagicMock) -> None:
    mock_run.side_effect = __import__(
        "subprocess", fromlist=["CalledProcessError"]
    ).CalledProcessError(1, "br", stderr="db locked")
    loop = BeadsLoop(_test_settings())
    assert loop._get_ready_beads("/workspace/proj") == []


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_invalid_json(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("garbage")
    loop = BeadsLoop(_test_settings())
    assert loop._get_ready_beads("/workspace/proj") == []


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_non_dict_non_list_data(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(json.dumps("just a string"))
    loop = BeadsLoop(_test_settings())
    assert loop._get_ready_beads("/workspace/proj") == []


# ── init guard (NOT_INITIALIZED is a normal state, not an error) ──────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_not_initialized_logs_info_once(
    mock_run: MagicMock, caplog
) -> None:
    """br ready NOT_INITIALIZED should log INFO once, then stay silent."""
    from subprocess import CalledProcessError

    mock_run.side_effect = CalledProcessError(
        1, "br", stderr='{"error":{"code":"NOT_INITIALIZED","retryable":false}}'
    )
    loop = BeadsLoop(_test_settings())

    with caplog.at_level("INFO"):
        assert loop._get_ready_beads("/workspace/proj") == []
        # Second call should not log again
        assert loop._get_ready_beads("/workspace/proj") == []

    init_logs = [r for r in caplog.records if "not initialized" in r.message.lower()]
    assert len(init_logs) == 1
    assert init_logs[0].levelname == "INFO"
    assert loop._logged_init_warning is True


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_get_ready_beads_other_error_logs_error(
    mock_run: MagicMock, caplog
) -> None:
    """Non-NOT_INITIALIZED errors should still log at ERROR level."""
    from subprocess import CalledProcessError

    mock_run.side_effect = CalledProcessError(1, "br", stderr="db locked")
    loop = BeadsLoop(_test_settings())

    with caplog.at_level("ERROR"):
        assert loop._get_ready_beads("/workspace/proj") == []

    error_logs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_logs) == 1
    assert loop._logged_init_warning is False


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_bvr_next_not_initialized_logs_info(
    mock_run: MagicMock, caplog
) -> None:
    """bvr --robot-next with 'no workspace config' should log INFO, not WARNING."""
    from subprocess import CalledProcessError

    mock_run.side_effect = CalledProcessError(
        1,
        "bvr",
        stderr="error: invalid argument: no workspace config or single-repo "
        "beads data could be resolved.",
    )
    loop = BeadsLoop(_test_settings())

    with caplog.at_level("INFO"):
        assert loop._get_next_bead_bvr("/workspace/proj") is None

    warning_logs = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warning_logs) == 0
    assert loop._logged_init_warning is True


# ── _select_next_bead ─────────────────────────────────────────────────────


def test_select_next_bead_picks_lowest_priority() -> None:
    loop = BeadsLoop(_test_settings())
    beads = [
        {"id": "br-low", "priority": 5},
        {"id": "br-high", "priority": 1},
        {"id": "br-mid", "priority": 3},
    ]
    selected = loop._select_next_bead(beads)
    assert selected is not None
    assert selected["id"] == "br-high"


def test_select_next_bead_empty() -> None:
    loop = BeadsLoop(_test_settings())
    assert loop._select_next_bead([]) is None


def test_select_next_bead_default_priority() -> None:
    loop = BeadsLoop(_test_settings())
    beads = [{"id": "br-x"}, {"id": "br-y", "priority": 1}]
    selected = loop._select_next_bead(beads)
    assert selected is not None
    assert selected["id"] == "br-y"


# ── _check_bead_status ────────────────────────────────────────────────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_closed(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps({"id": "br-x", "status": "closed"})
    )
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-x", "/workspace/proj") == "closed"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_open(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(
        json.dumps({"issue": {"id": "br-y", "status": "open"}})
    )
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-y", "/workspace/proj") == "open"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_unknown_on_error(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError()
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-z", "/workspace/proj") == "unknown"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_empty_stdout(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("")
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-z", "/workspace/proj") == "unknown"


# ── _process_bead ─────────────────────────────────────────────────────────


def test_retry_halt_after_max() -> None:
    settings = _test_settings(beads_max_retries=2)
    loop = BeadsLoop(settings)
    bead = {"id": "br-retry", "title": "T", "priority": 1}
    loop._retry_state["proj:br-retry"] = {"count": 2, "logs": "error"}

    with patch.object(loop, "_spawn_agent") as mock_spawn:
        loop._process_bead(bead, "proj", "/workspace/proj")
        mock_spawn.assert_not_called()


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
def test_process_bead_success(
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    mock_spawn.return_value = (True, "")
    mock_status.return_value = "closed"
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-ok"

    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-ok", "title": "Task", "priority": 1, "description": "Do work"}
    loop._process_bead(bead, "proj", "/workspace/proj")

    assert "proj:br-ok" not in loop._retry_state
    mock_spawn.assert_called_once()


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
def test_process_bead_failure_increments_retry(
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    mock_spawn.return_value = (False, "error output")
    mock_status.return_value = "open"
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-fail"

    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-fail", "title": "Task", "priority": 1}
    loop._process_bead(bead, "proj", "/workspace/proj")

    assert loop._retry_state["proj:br-fail"]["count"] == 1
    assert loop._retry_state["proj:br-fail"]["logs"] == "error output"


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
def test_process_bead_pushes_and_creates_pr(
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    mock_spawn.return_value = (True, "")
    mock_status.return_value = "closed"
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-ws"

    with (
        patch("webhook_receiver.beads_loop.push_branch") as mock_push,
        patch("webhook_receiver.beads_loop.create_pr") as mock_pr,
    ):
        loop = BeadsLoop(_test_settings())
        bead = {"id": "br-ws", "title": "WS Task", "priority": 1}
        loop._process_bead(bead, "proj", "/workspace/proj")

        mock_create_wt.assert_called_once()
        mock_push.assert_called_once()
        mock_pr.assert_called_once()
        mock_remove_wt.assert_called_once()
        assert "proj:br-ws" not in loop._retry_state


@patch("webhook_receiver.beads_loop.create_bead_worktree")
def test_process_bead_worktree_creation_failure(
    mock_create_wt: MagicMock,
) -> None:
    mock_create_wt.side_effect = Exception("worktree failed")

    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-wsfail", "title": "T", "priority": 1}

    with patch.object(loop, "_spawn_agent") as mock_spawn:
        loop._process_bead(bead, "proj", "/workspace/proj")
        mock_spawn.assert_not_called()
        assert loop._retry_state["proj:br-wsfail"]["count"] == 1


@patch("webhook_receiver.beads_loop.remove_bead_worktree")
@patch("webhook_receiver.beads_loop.create_bead_worktree")
@patch("webhook_receiver.beads_loop.BeadsLoop._spawn_agent")
@patch("webhook_receiver.beads_loop.BeadsLoop._check_bead_status")
def test_process_bead_push_failure_still_clears_retry(
    mock_status: MagicMock,
    mock_spawn: MagicMock,
    mock_create_wt: MagicMock,
    mock_remove_wt: MagicMock,
) -> None:
    mock_spawn.return_value = (True, "")
    mock_status.return_value = "closed"
    mock_create_wt.return_value = "/workspace/proj/.worktrees/br-push"

    with (
        patch("webhook_receiver.beads_loop.push_branch", side_effect=Exception("push failed")),
        patch("webhook_receiver.beads_loop.create_pr"),
    ):
        loop = BeadsLoop(_test_settings())
        bead = {"id": "br-push", "title": "Push Task", "priority": 1}
        loop._process_bead(bead, "proj", "/workspace/proj")

        assert "proj:br-push" not in loop._retry_state


# ── stop ──────────────────────────────────────────────────────────────────


def test_stop_sets_running_false() -> None:
    loop = BeadsLoop(_test_settings())
    loop._running = True
    loop.stop()
    assert loop._running is False


# ── _poll_and_process_project ─────────────────────────────────────────────


def test_poll_and_process_project_no_beads() -> None:
    loop = BeadsLoop(_test_settings())
    with (
        patch.object(loop, "_get_next_bead", return_value=None),
        patch.object(loop, "_log_overview_if_idle") as mock_overview,
    ):
        loop._poll_and_process_project("proj", "/workspace/proj")
        mock_overview.assert_called_once()


def test_poll_and_process_project_processes_bead() -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-poll", "title": "T", "priority": 1}
    with (
        patch.object(loop, "_get_next_bead", return_value=bead),
        patch.object(loop, "_process_bead") as mock_process,
    ):
        loop._poll_and_process_project("proj", "/workspace/proj")
        mock_process.assert_called_once_with(bead, "proj", "/workspace/proj")
        assert "br-poll" not in loop._active_beads


def test_poll_and_process_project_skips_already_active() -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-active", "title": "T", "priority": 1}
    loop._active_beads.add("proj:br-active")
    with (
        patch.object(loop, "_get_next_bead", return_value=bead),
        patch.object(loop, "_process_bead") as mock_process,
    ):
        loop._poll_and_process_project("proj", "/workspace/proj")
        mock_process.assert_not_called()


def test_poll_and_process_project_skips_bead_without_id() -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"title": "No ID bead"}
    with (
        patch.object(loop, "_get_next_bead", return_value=bead),
        patch.object(loop, "_process_bead") as mock_process,
    ):
        loop._poll_and_process_project("proj", "/workspace/proj")
        mock_process.assert_not_called()


def test_poll_and_process_project_releases_lock_on_exception() -> None:
    loop = BeadsLoop(_test_settings())
    bead = {"id": "br-exc", "title": "T", "priority": 1}
    with (
        patch.object(loop, "_get_next_bead", return_value=bead),
        patch.object(loop, "_process_bead", side_effect=RuntimeError("boom")),
    ):
        try:
            loop._poll_and_process_project("proj", "/workspace/proj")
        except RuntimeError:
            pass
        assert "br-exc" not in loop._active_beads


# ── _scan_and_process (multi-project discovery) ───────────────────────────


def test_scan_and_process_discovers_projects(tmp_path) -> None:
    """_scan_and_process discovers projects and calls _poll_and_process_project."""
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "proj-a", ".beads"))
    os.makedirs(os.path.join(base, "proj-b", ".beads"))

    settings = _test_settings(beads_workspace_root=base)
    loop = BeadsLoop(settings)
    with patch.object(loop, "_poll_and_process_project") as mock_poll:
        loop._scan_and_process()
        slugs_polled = [call.args[0] for call in mock_poll.call_args_list]
        assert set(slugs_polled) == {"proj-a", "proj-b"}
        assert mock_poll.call_count == 2


def test_scan_and_process_no_projects() -> None:
    """No projects found → no polling."""
    settings = _test_settings(beads_workspace_root="/nonexistent")
    loop = BeadsLoop(settings)
    with patch.object(loop, "_poll_and_process_project") as mock_poll:
        loop._scan_and_process()
        mock_poll.assert_not_called()


# ── _check_bead_status additional error paths ─────────────────────────────


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_invalid_json(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result("not json")
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-q", "/workspace/proj") == "unknown"


@patch("webhook_receiver.beads_loop.subprocess.run")
def test_check_bead_status_non_dict_data(mock_run: MagicMock) -> None:
    mock_run.return_value = _mock_result(json.dumps([1, 2, 3]))
    loop = BeadsLoop(_test_settings())
    assert loop._check_bead_status("br-r", "/workspace/proj") == "unknown"
