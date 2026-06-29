from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from webhook_receiver.workspace import (
    create_bead_worktree,
    create_pr,
    discover_projects,
    ensure_project_from_clone,
    init_project_workspace,
    project_workspace_path,
    push_branch,
    remove_bead_worktree,
)

# ── project paths ──────────────────────────────────────────────────────────


def test_project_workspace_path_joins_base_and_slug() -> None:
    assert project_workspace_path("/workspace", "my-app") == "/workspace/my-app"
    assert project_workspace_path("/ws", "owner/repo") == "/ws/owner-repo"


# ── discover_projects ──────────────────────────────────────────────────────


def test_discover_projects_finds_beads_dirs(tmp_path: str) -> None:
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "proj-a", ".beads"))
    os.makedirs(os.path.join(base, "proj-b", ".beads"))
    os.makedirs(os.path.join(base, "not-a-project"))  # no .beads
    os.makedirs(os.path.join(base, ".hidden", ".beads"))  # hidden, skipped

    result = discover_projects(base)
    assert result == ["proj-a", "proj-b"]


def test_discover_projects_empty_base(tmp_path: str) -> None:
    assert discover_projects(str(tmp_path)) == []


def test_discover_projects_missing_base() -> None:
    assert discover_projects("/nonexistent/path") == []


# ── init_project_workspace ─────────────────────────────────────────────────


@patch("webhook_receiver.workspace.subprocess.run")
def test_init_project_workspace_creates_dir_and_git_init(
    mock_run: MagicMock, tmp_path: str
) -> None:
    base = str(tmp_path)
    slug = "new-proj"

    result = init_project_workspace(base, slug)

    assert result == os.path.join(base, slug)
    assert os.path.isdir(result)
    # git init should have been called
    init_calls = [
        c for c in mock_run.call_args_list if "init" in (c.args[0] if c.args else [])
    ]
    assert len(init_calls) == 1
    assert init_calls[0].kwargs["cwd"] == result


def test_init_project_workspace_excludes_worktrees(tmp_path: str) -> None:
    base = str(tmp_path)
    slug = "excluded-proj"
    os.makedirs(os.path.join(base, slug, ".git", "info"))

    init_project_workspace(base, slug)

    exclude_path = os.path.join(base, slug, ".git", "info", "exclude")
    with open(exclude_path, encoding="utf-8") as f:
        content = f.read()
    assert ".worktrees/" in content


# ── ensure_project_from_clone ──────────────────────────────────────────────


@patch("webhook_receiver.workspace.subprocess.run")
def test_ensure_project_from_clone_clones(
    mock_run: MagicMock, tmp_path: str
) -> None:
    base = str(tmp_path)
    slug = "cloned-proj"
    repo_url = "https://github.com/o/r.git"

    result = ensure_project_from_clone(base, slug, repo_url)

    assert result == os.path.join(base, slug)
    clone_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and "clone" in c.args[0]
    ]
    assert len(clone_calls) == 1


@patch("webhook_receiver.workspace.subprocess.run")
def test_ensure_project_from_clone_idempotent(
    mock_run: MagicMock, tmp_path: str
) -> None:
    base = str(tmp_path)
    slug = "existing-proj"
    # Simulate an already-cloned repo
    project_root = os.path.join(base, slug)
    os.makedirs(os.path.join(project_root, ".git"))

    result = ensure_project_from_clone(base, slug, "https://github.com/o/r.git")

    assert result == project_root
    # No clone should have been performed
    clone_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and "clone" in c.args[0]
    ]
    assert len(clone_calls) == 0


# ── create_bead_worktree ───────────────────────────────────────────────────


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_bead_worktree_creates_branch(
    mock_run: MagicMock, tmp_path: str
) -> None:
    project_root = str(tmp_path)
    bead_id = "br-test123"

    result = create_bead_worktree(project_root, bead_id)

    assert result == os.path.join(project_root, ".worktrees", bead_id)
    # git worktree add should have been called with the branch
    wt_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and "worktree" in c.args[0]
    ]
    assert len(wt_calls) == 1
    cmd = wt_calls[0].args[0]
    assert "worktree" in cmd
    assert "add" in cmd
    assert "-b" in cmd
    assert f"task/{bead_id}" in cmd
    assert wt_calls[0].kwargs["cwd"] == project_root


@patch("webhook_receiver.workspace.subprocess.run")
def test_create_bead_worktree_removes_stale(
    mock_run: MagicMock, tmp_path: str
) -> None:
    project_root = str(tmp_path)
    bead_id = "br-stale"
    wt_path = os.path.join(project_root, ".worktrees", bead_id)
    os.makedirs(wt_path)

    create_bead_worktree(project_root, bead_id)

    # Should include a worktree remove call before the add
    wt_remove_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "worktree", "remove"]
    ]
    assert len(wt_remove_calls) >= 1


# ── remove_bead_worktree ───────────────────────────────────────────────────


@patch("webhook_receiver.workspace.subprocess.run")
def test_remove_bead_worktree_noop_if_missing(
    mock_run: MagicMock, tmp_path: str
) -> None:
    remove_bead_worktree(str(tmp_path), "br-nonexistent")
    # No worktree remove calls should be made
    wt_calls = [
        c
        for c in mock_run.call_args_list
        if c.args and c.args[0][:3] == ["git", "worktree", "remove"]
    ]
    assert len(wt_calls) == 0


def test_remove_bead_worktree_rmtree_fallback(tmp_path: str) -> None:
    project_root = str(tmp_path)
    bead_id = "br-fallback"
    wt_path = os.path.join(project_root, ".worktrees", bead_id)
    os.makedirs(wt_path)
    open(os.path.join(wt_path, "file"), "w").close()

    remove_bead_worktree(project_root, bead_id)

    assert not os.path.exists(wt_path)


# ── push_branch & create_pr (unchanged interface) ─────────────────────────


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
