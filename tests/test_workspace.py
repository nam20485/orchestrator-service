from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from webhook_receiver.workspace import (
    cleanup_workspace,
    create_pr,
    create_workspace,
    push_branch,
    workspace_path,
)


def test_workspace_path_joins_root_and_sanitized_id() -> None:
    assert workspace_path("/workspace", "br-abc123") == "/workspace/br-abc123"
    assert workspace_path("/ws", "br/with/slash") == "/ws/br-with-slash"


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_workspace_clones_and_branches(
    mock_run: MagicMock, tmp_path: str
) -> None:
    ws_root = str(tmp_path)
    bead_id = "br-test123"
    repo_url = "https://github.com/org/repo.git"

    result = create_workspace(ws_root, bead_id, repo_url)

    assert result == os.path.join(ws_root, bead_id)
    assert mock_run.call_count == 2

    clone_call = mock_run.call_args_list[0]
    assert clone_call.args[0] == [
        "git", "clone", "--branch", "main", repo_url, result
    ]

    branch_call = mock_run.call_args_list[1]
    assert branch_call.args[0] == ["git", "checkout", "-b", f"task/{bead_id}"]
    assert branch_call.kwargs["cwd"] == result


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_workspace_removes_existing_dir(
    mock_run: MagicMock, tmp_path: str
) -> None:
    ws_root = str(tmp_path)
    bead_id = "br-test456"
    os.makedirs(os.path.join(ws_root, bead_id))
    sentinel = os.path.join(ws_root, bead_id, "old_file")
    open(sentinel, "w").close()

    create_workspace(ws_root, bead_id, "https://github.com/o/r.git")

    assert not os.path.exists(sentinel)


@patch("webhook_receiver.workspace.subprocess.run")
def test_push_branch_calls_git_push(mock_run: MagicMock) -> None:
    push_branch("/some/path", "br-abc")
    mock_run.assert_called_once_with(
        ["git", "push", "origin", "task/br-abc"],
        cwd="/some/path",
        check=True,
        capture_output=True,
        text=True,
    )


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_pr_calls_gh(mock_run: MagicMock) -> None:
    create_pr("/some/path", "br-abc", "My Task")
    mock_run.assert_called_once()
    args = mock_run.call_args.args[0]
    assert args[0:3] == ["gh", "pr", "create"]
    assert "--title" in args
    assert "Implement br-abc: My Task" in args


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_pr_custom_body(mock_run: MagicMock) -> None:
    create_pr("/p", "br-x", "T", body="custom body")
    args = mock_run.call_args.args[0]
    assert "custom body" in args


def test_cleanup_workspace_removes_dir(tmp_path: str) -> None:
    ws_root = str(tmp_path)
    bead_id = "br-cleanup"
    ws = os.path.join(ws_root, bead_id)
    os.makedirs(ws)
    open(os.path.join(ws, "file"), "w").close()

    cleanup_workspace(ws_root, bead_id)

    assert not os.path.exists(ws)


def test_cleanup_workspace_noop_if_missing(tmp_path: str) -> None:
    cleanup_workspace(str(tmp_path), "br-nonexistent")
