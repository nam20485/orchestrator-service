"""Multi-project workspace management for beads execution.

Each project lives in its own subdirectory under the workspace base dir
(``BEADS_WORKSPACE_ROOT``).  A project directory is a git repository
(either a clone of an existing repo or a fresh ``git init``) that holds:

* ``.beads/beads.db`` — the project's beads DAG
* ``.worktrees/<bead_id>/`` — per-bead isolated git worktrees (never shared)

The ``BeadsLoop`` scans the base dir for project subdirs (those containing a
``.beads/`` directory) and processes each project independently.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_WORKTREES_DIR = ".worktrees"
_WORKTREES_EXCLUDE_LINE = ".worktrees/"


# ── project discovery & paths ──────────────────────────────────────────────


def project_workspace_path(base_dir: str, slug: str) -> str:
    """Return the project workspace directory path under *base_dir*."""
    safe_slug = slug.replace("/", "-")
    path = os.path.join(base_dir, safe_slug)
    # Containment guard: the resolved path must stay within base_dir.
    _assert_within_base(base_dir, path)
    return path


def _assert_within_base(base_dir: str, path: str) -> None:
    """Raise ValueError if *path* resolves outside *base_dir*."""
    base_real = os.path.realpath(base_dir)
    path_real = os.path.realpath(path)
    if not (path_real == base_real or path_real.startswith(base_real + os.sep)):
        raise ValueError(f"Path '{path}' escapes workspace base '{base_dir}'")


def discover_projects(base_dir: str) -> list[str]:
    """Scan *base_dir*/*/ for subdirs containing ``.beads/``.

    Returns a sorted list of project slugs (directory names).  Hidden dirs
    and the worktrees directory itself are skipped.

    If no projects are found but a legacy ``.beads/`` exists directly at
    *base_dir* (pre-multi-project layout), logs a prominent WARNING so
    operators know to migrate.
    """
    if not os.path.isdir(base_dir):
        return []
    slugs: list[str] = []
    for entry in os.listdir(base_dir):
        if entry.startswith("."):
            continue
        full = os.path.join(base_dir, entry)
        if not os.path.isdir(full):
            continue
        if os.path.isdir(os.path.join(full, ".beads")):
            slugs.append(entry)
    slugs.sort()

    # Legacy layout detection: .beads/ directly at the base (pre-multi-project).
    if not slugs and os.path.isdir(os.path.join(base_dir, ".beads")):
        logger.warning(
            "Legacy single-project .beads/ detected at %s — no project subdirs "
            "found. Move .beads/ into a project subdir (e.g. %s/<project-slug>/.beads/) "
            "to make it discoverable by the multi-project BeadsLoop.",
            base_dir,
            base_dir,
        )
    return slugs


def _ensure_worktrees_excluded(project_root: str) -> None:
    """Append ``.worktrees/`` to ``.git/info/exclude`` so worktrees are not tracked.

    This modifies only the *local* exclude file (never the committed
    ``.gitignore``), so project files stay untouched.
    """
    exclude_path = os.path.join(project_root, ".git", "info", "exclude")
    lines: list[str] = []
    if os.path.isfile(exclude_path):
        try:
            lines = Path(exclude_path).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    if _WORKTREES_EXCLUDE_LINE not in lines:
        lines.append(_WORKTREES_EXCLUDE_LINE)
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        Path(exclude_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_project_workspace(base_dir: str, slug: str) -> str:
    """Create a new project workspace at ``base_dir/<slug>/`` and ``git init`` it.

    Appends ``.worktrees/`` to the local git exclude so per-bead worktrees
    do not show up as untracked content.  Returns the project path.
    """
    project_root = project_workspace_path(base_dir, slug)
    os.makedirs(project_root, exist_ok=True)

    if not os.path.isdir(os.path.join(project_root, ".git")):
        logger.info("git init for project=%s at %s", slug, project_root)
        subprocess.run(
            ["git", "init", "--initial-branch=main"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )

    _ensure_worktrees_excluded(project_root)
    return project_root


def ensure_project_from_clone(
    base_dir: str,
    slug: str,
    repo_url: str,
    base_branch: str = "main",
) -> str:
    """Ensure a project workspace exists for *slug*, cloning *repo_url* if needed.

    If the project dir already exists (previously cloned) it is returned
    as-is.  Otherwise a fresh clone is performed.  ``.worktrees/`` is
    added to the local exclude either way.
    """
    project_root = project_workspace_path(base_dir, slug)
    if os.path.isdir(os.path.join(project_root, ".git")):
        _ensure_worktrees_excluded(project_root)
        return project_root

    os.makedirs(base_dir, exist_ok=True)
    if os.path.exists(project_root):
        shutil.rmtree(project_root)

    logger.info(
        "Cloning repo=%s branch=%s into %s for project=%s",
        repo_url,
        base_branch,
        project_root,
        slug,
    )
    subprocess.run(
        ["git", "clone", "--branch", base_branch, repo_url, project_root],
        check=True,
        capture_output=True,
        text=True,
    )
    _ensure_worktrees_excluded(project_root)
    return project_root


def sync_project(repo_path: str, branch: str = "main") -> None:
    """Best-effort ``git fetch + pull`` to refresh a project workspace.

    Logs on failure but never raises — a stale checkout is still usable.
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "checkout", branch],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "pull", "origin", branch],
            cwd=repo_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("sync_project failed for %s: %s", repo_path, exc)


# ── per-bead worktree management ───────────────────────────────────────────


def _detect_default_branch(project_root: str) -> str:
    """Return the repo's current default branch via ``git symbolic-ref``.

    Falls back to ``"main"`` if git is unavailable, the repo has no HEAD
    (e.g. unborn branch), or the command fails for any reason.
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        branch = result.stdout.strip()
        if branch:
            return branch
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.debug(
            "Could not detect default branch in %s; falling back to 'main'",
            project_root,
            exc_info=True,
        )
    return "main"


def create_bead_worktree(
    project_root: str,
    bead_id: str,
    base_branch: str | None = None,
) -> str:
    """Create an isolated git worktree for *bead_id* inside the project.

    The worktree is placed at ``<project_root>/.worktrees/<bead_id>/`` on a
    new branch ``task/<bead_id>``.  Any stale worktree at that path is
    removed first.  Returns the worktree path.

    When *base_branch* is ``None`` (the default), the repo's current
    default branch is auto-detected via :func:`_detect_default_branch` so
    repos whose default branch is ``master`` (or anything else) work
    without configuration.  Pass an explicit *base_branch* to override.
    """
    wt_dir = os.path.join(project_root, _WORKTREES_DIR)
    os.makedirs(wt_dir, exist_ok=True)

    safe_id = bead_id.replace("/", "-")
    wt_path = os.path.join(wt_dir, safe_id)
    branch_name = f"task/{bead_id}"
    branch = base_branch if base_branch is not None else _detect_default_branch(project_root)

    # Remove a stale worktree if one exists.
    remove_bead_worktree(project_root, bead_id)

    logger.info(
        "Creating worktree %s (branch %s) for bead=%s in project=%s",
        wt_path,
        branch_name,
        bead_id,
        os.path.basename(project_root),
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, wt_path, branch],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return wt_path


def remove_bead_worktree(project_root: str, bead_id: str) -> None:
    """Remove the worktree and branch for *bead_id* from *project_root*.

    ``git worktree remove --force`` handles the worktree; the branch is
    deleted as best-effort.  A rmtree fallback ensures no stale dir
    remains even if the worktree command fails.
    """
    safe_id = bead_id.replace("/", "-")
    wt_path = os.path.join(project_root, _WORKTREES_DIR, safe_id)
    branch_name = f"task/{bead_id}"

    if os.path.exists(wt_path):
        logger.info("Removing worktree %s for bead=%s", wt_path, bead_id)
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt_path],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            logger.debug("git worktree remove failed; rmtree fallback", exc_info=True)
            shutil.rmtree(wt_path, ignore_errors=True)

        # Best-effort branch cleanup.
        try:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass


# ── git push & PR (unchanged — operate on a worktree checkout) ─────────────


def push_branch(ws_path: str, bead_id: str) -> None:
    """Push the task branch to origin from *ws_path* (a worktree)."""
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
