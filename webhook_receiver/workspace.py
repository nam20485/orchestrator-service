from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def workspace_path(workspace_root: str, bead_id: str) -> str:
    """Return the per-bead workspace directory path under *workspace_root*."""
    safe_id = bead_id.replace("/", "-")
    return os.path.join(workspace_root, safe_id)


def create_workspace(
    workspace_root: str,
    bead_id: str,
    repo_url: str,
    base_branch: str = "main",
) -> str:
    """Clone *repo_url* into ``{workspace_root}/{bead_id}/`` and create a task branch.

    Removes any existing directory for a clean start.
    Returns the workspace path.
    """
    ws_path = workspace_path(workspace_root, bead_id)
    branch_name = f"task/{bead_id}"

    os.makedirs(workspace_root, exist_ok=True)

    if os.path.exists(ws_path):
        shutil.rmtree(ws_path)

    logger.info(
        "Cloning repo=%s branch=%s into %s for bead=%s",
        repo_url,
        base_branch,
        ws_path,
        bead_id,
    )

    subprocess.run(
        ["git", "clone", "--branch", base_branch, repo_url, ws_path],
        check=True,
        capture_output=True,
        text=True,
    )

    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=ws_path,
        check=True,
        capture_output=True,
        text=True,
    )

    return ws_path


def push_branch(ws_path: str, bead_id: str) -> None:
    """Push the task branch to origin."""
    branch_name = f"task/{bead_id}"
    logger.info("Pushing branch %s from %s", branch_name, ws_path)
    subprocess.run(
        ["git", "push", "origin", branch_name],
        cwd=ws_path,
        check=True,
        capture_output=True,
        text=True,
    )


def create_pr(ws_path: str, bead_id: str, title: str, body: str = "") -> None:
    """Create a pull request via ``gh`` for the task branch."""
    pr_title = f"Implement {bead_id}: {title}"
    pr_body = body or f"Automated implementation for bead {bead_id}: {title}"

    logger.info("Creating PR for bead=%s title=%s", bead_id, pr_title)
    subprocess.run(
        ["gh", "pr", "create", "--title", pr_title, "--body", pr_body],
        cwd=ws_path,
        check=True,
        capture_output=True,
        text=True,
    )


def cleanup_workspace(workspace_root: str, bead_id: str) -> None:
    """Remove the per-bead workspace directory."""
    ws_path = workspace_path(workspace_root, bead_id)
    if os.path.exists(ws_path):
        logger.info("Cleaning up workspace %s for bead=%s", ws_path, bead_id)
        shutil.rmtree(ws_path, ignore_errors=True)
