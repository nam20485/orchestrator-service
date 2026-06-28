# Multi-Project Workspace Isolation

## Problem

The orchestrator service treats `/workspace` as a single global workspace. Two consequences:

1. **Shared-root bug (beads path):** `BeadsLoop._process_bead` (beads_loop.py:248) falls back to `ws_path = ws_root` when `BEADS_TARGET_REPO` is empty (the default). Every bead agent works in the same `/workspace` directory — same git index, same branch, same files. This contradicts every architecture plan, which required per-bead isolation via subdirectories. It also makes parallel bead execution impossible.

2. **Static repo config makes no sense:** `BEADS_TARGET_REPO` (config.py:92) is a static compose env var. The service is long-running and handles many repos over its lifetime. A single static repo URL is fundamentally wrong.

3. **Webhook path shares the same root:** `dispatch_to_opencode` (runner.py) uses `settings.workspace` (`/workspace`) for all webhook-driven agent runs. No concept of which repo the work belongs to.

4. **No project concept:** The service has no notion of "which project/repo does this work belong to." Both dispatch paths (webhook label-driven, beads DAG) share `/workspace` with no isolation.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Workspace structure | `/workspace/<project-slug>/` per project | Multi-repo, long-running service |
| Per-bead isolation | **Git worktree** at `.worktrees/<bead-id>/` | Zero-copy (shares `.git`), instant, works without a remote (bootstrap case), native branch isolation, clean `git worktree remove` cleanup |
| `.beads/` location | Per-project: `/workspace/<slug>/.beads/beads.db` | Each project has its own DAG |
| `BEADS_TARGET_REPO` | **Eliminated** | Repo identity comes from context, not static config |
| BeadsLoop discovery | Filesystem scan of `/workspace/*/` for `.beads/` dirs | No extra state; projects self-register |
| Scope | **Both** beads + webhook paths | Avoids inconsistent workspace models |
| Bootstrap (no repo yet) | Project dir `git init`-ed on creation | Worktrees work from a local repo even without a remote |
| Project slug (webhook) | `repository.full_name` sanitized (e.g., `owner-repo`) | Derived from payload |
| Project slug (/perfect-idea) | Client script `-Project <slug>` parameter | User provides at session start |
| Project creation (new) | Client script creates dir + `git init` | Before /perfect-idea runs |
| Project creation (existing) | Webhook handler clones on first event for that repo | Ensures workspace before dispatch |

## Target Workspace Layout

```
/workspace/                          # BEADS_WORKSPACE_ROOT (scan base)
  <project-slug>/                    # project workspace (git repo)
    .beads/
      beads.db                       # this project's DAG
    .git/                            # git repo (cloned or git-init'd)
    .git/info/exclude                # contains ".worktrees/" entry
    .worktrees/                      # per-bead git worktrees (gitignored locally)
      <bead-id>/                     # git worktree on branch task/<bead-id>
    plan_docs/
      application_plan.md            # committed so worktrees can see it
    (project code)
```

## Task Breakdown

### Phase 1: Workspace layer (`webhook_receiver/workspace.py`)

Refactor `workspace.py` from single-root clone model to multi-project worktree model.

**New functions:**

- `discover_projects(base_dir: str) -> list[str]`
  - Scan `base_dir/*/` for subdirs containing `.beads/`.
  - Return list of project slugs (directory names).
  - Skip hidden dirs (`.worktrees`, etc.).

- `project_workspace_path(base_dir: str, slug: str) -> str`
  - Return `os.path.join(base_dir, slug)`.

- `init_project_workspace(base_dir: str, slug: str) -> str`
  - Create `base_dir/<slug>/`.
  - Run `git init` in it.
  - Append `.worktrees/` to `.git/info/exclude` (local-only gitignore, does not modify committed files).
  - Return the project workspace path.

- `ensure_project_from_clone(base_dir: str, slug: str, repo_url: str, base_branch: str = "main") -> str`
  - If `base_dir/<slug>/` exists, return it (already cloned).
  - If not, `git clone --branch <base_branch> <repo_url> base_dir/<slug>/`.
  - Append `.worktrees/` to `.git/info/exclude`.
  - Return the project workspace path.

- `sync_project(repo_path: str, branch: str = "main") -> None`
  - `git fetch origin && git checkout <branch> && git pull` (best-effort; log on failure, don't raise).

**Refactored functions:**

- `create_workspace()` → `create_bead_worktree(project_root: str, bead_id: str, base_branch: str = "main") -> str`
  - Ensure `.worktrees/` exists in `project_root`.
  - Remove any stale worktree at `.worktrees/<bead_id>/` (worktree remove + rmtree fallback).
  - `git worktree add .worktrees/<bead_id> -b task/<bead_id> <base_branch>` (run from `project_root`).
  - Return `project_root/.worktrees/<bead_id>`.

- `cleanup_workspace()` → `remove_bead_worktree(project_root: str, bead_id: str) -> None`
  - `git worktree remove .worktrees/<bead-id> --force` (run from `project_root`).
  - `git branch -D task/<bead-id>` (clean up the branch; best-effort).
  - Rmtree fallback if worktree command fails.

- `push_branch(ws_path: str, bead_id: str)` — unchanged (runs from worktree, pushes `task/<bead_id>` to origin).

- `create_pr(ws_path: str, bead_id: str, title: str, body: str = "")` — unchanged.

**Keep:** `workspace_path()` deprecated or removed (replaced by `project_workspace_path` + `create_bead_worktree`).

### Phase 2: Config (`webhook_receiver/config.py`, `compose.yaml`)

- **Remove** `beads_target_repo` field from `Settings` and `from_env()`.
- **Keep** `beads_workspace_root` — its meaning changes from "the beads workspace" to "base dir containing project workspaces" (default `/workspace`). Consider renaming to `workspace_base` for clarity (optional).
- **compose.yaml:** Remove `BEADS_TARGET_REPO=${BEADS_TARGET_REPO:-}` from webhook-receiver environment.
- **compose.development.yaml:** Same removal.

### Phase 3: BeadsLoop multi-project (`webhook_receiver/beads_loop.py`)

Major refactor from single-project serial loop to multi-project scanner.

**State changes:**
- `_active_beads`, `_retry_state`, `_bead_start_times`, `_halted_beads` → keyed by `(project_slug, bead_id)` or nested `dict[str, set[str]]` keyed by project slug.
- Add `_project_locks: dict[str, threading.Lock]` for per-project serialization (prevents concurrent clone/sync within a project during a single poll).

**Loop restructure (`run` / `_poll_and_process`):**
```
while running:
    projects = discover_projects(beads_workspace_root)
    for project_slug in projects:
        project_root = project_workspace_path(base, project_slug)
        poll_and_process_project(project_slug, project_root)
    sleep(poll_interval)
```

**`_poll_and_process_project(project_slug, project_root)`:**
- Same logic as current `_poll_and_process` but scoped to one project.
- `_run_beads_cmd(args)` now takes `cwd=project_root` (add `cwd` parameter or make it an instance method with project context).
- `BD_DB` env var = `<project_root>/.beads/beads.db`.
- On success: create worktree via `create_bead_worktree(project_root, bead_id)`.
- Spawn agent with workspace = worktree path.
- After agent: push + PR from worktree, then `remove_bead_worktree(project_root, bead_id)`.

**`_spawn_agent` changes:**
- Accept `project_root` parameter.
- `BD_DB` = `os.path.join(project_root, ".beads", "beads.db")`.
- Worktree path = `create_bead_worktree(project_root, bead_id)` (replaces `create_workspace`).
- Context files (`BEADS_AGENT_GUIDE.md`, etc.) written to the worktree path.

**`_run_beads_cmd` changes:**
- Accept `cwd` parameter (defaults to current behavior for backward compat).
- Or refactor to take project_root from caller.

**Dashboard properties (`active_beads`, `retry_state`, etc.):**
- Return per-project data or flattened with project slug included.

### Phase 4: Webhook path (`webhook_receiver/app.py`, `webhook_receiver/runner.py`)

**`app.py` webhook handler changes:**
- After signature validation and payload parsing, extract repo info:
  ```python
  repo_full_name = payload.get("repository", {}).get("full_name", "")
  clone_url = payload.get("repository", {}).get("clone_url", "")
  ```
- Derive project slug: `slug = repo_full_name.replace("/", "-")` (e.g., `owner-repo`).
- Ensure project workspace:
  ```python
  if clone_url:
      project_root = ensure_project_from_clone(base_dir, slug, clone_url)
      sync_project(project_root)  # best-effort pull
  ```
- Create modified settings with `workspace=project_root`:
  ```python
  project_settings = replace(cfg, workspace=project_root)
  ```
- Dispatch with project settings:
  ```python
  background_tasks.add_task(dispatch_to_opencode, project_settings, prompt, store)
  ```
- Emit events with `project_slug` for dashboard visibility.

**`runner.py` changes:**
- `dispatch_to_opencode` already uses `settings.workspace` for `-Workspace`. No change needed — it'll pick up the project-specific workspace from the modified settings.
- Add `project_slug` to log messages and event emissions for traceability.

### Phase 5: Client scripts (`scripts/prompt.ps1`, `scripts/attach.ps1`)

**Add `-Project <slug>` parameter to both scripts:**

- When `-Project` is provided:
  - Compute project workspace: `$Workspace = Join-Path $WorkspaceRoot $Project`
  - If not exists: `mkdir` + `git init` + add `.worktrees/` to `.git/info/exclude`
  - Pass `--dir $Workspace` to opencode
- When `-Project` is not provided:
  - Backward-compatible: use `$WorkspaceRoot` directly (deprecated, log warning)

This enables the `/perfect-idea` flow:
```
scripts/attach.ps1 -Project my-new-app
# → creates /workspace/my-new-app/ + git init
# → opencode attach --dir /workspace/my-new-app/
# user runs /perfect-idea → plan_docs/application_plan.md
# user runs /plan-to-beads → .beads/
# BeadsLoop discovers project on next scan
```

### Phase 6: Skills (`image/.opencode/skills/`)

**`perfect-idea/SKILL.md`:**
- No structural change needed (the client script creates the workspace). But add a note in Phase 2 (Generation): commit `plan_docs/application_plan.md` to the repo so worktrees can see it:
  ```bash
  git add plan_docs/application_plan.md
  git commit -m "Add application plan"
  ```

**`plan-to-beads/SKILL.md`:**
- Already commits `.beads/` (Step 5: Execute and Commit). Ensure the commit also includes `plan_docs/` if not already committed.
- Add note: "This skill runs inside a project workspace. The `.beads/` directory is created here and the BeadsLoop will discover this project automatically."

**`bead_context.py`:**
- `TOOLING_REFERENCE` note about "run br from this workspace directory" — update to reflect that `BD_DB` is set by the orchestrator, so `br close` works from the worktree.

### Phase 7: Tests

**Update existing tests:**
- `tests/test_beads_loop.py` — multi-project: mock `discover_projects`, test per-project processing.
- `tests/test_workspace.py` — test `create_bead_worktree`, `remove_bead_worktree`, `discover_projects`, `init_project_workspace`, `ensure_project_from_clone`.
- `tests/test_config.py` — remove `BEADS_TARGET_REPO` from required fields list.
- `tests/test_app.py` — webhook handler derives project slug, ensures workspace.
- `tests/test_runner.py` — dispatch uses project workspace.
- `tests/test_dashboard.py` — per-project active beads.
- `tests/test_integration_beads.py` — multi-project integration.
- `tests/test_integration_webhook.py` — webhook creates project workspace.

**New tests:**
- `test_discover_projects_finds_beads_dirs` — scans `/workspace/*/` for `.beads/`.
- `test_init_project_workspace_creates_git_repo` — `git init` + `.git/info/exclude`.
- `test_ensure_project_from_clone_idempotent` — second call returns existing dir.
- `test_create_bead_worktree_isolates_branch` — worktree on `task/<bead-id>`, separate from main.
- `test_remove_bead_worktree_cleans_up` — worktree + branch removed.
- `test_webhook_derives_slug_from_payload` — `owner/repo` → `owner-repo`.
- `test_beads_loop_processes_multiple_projects` — two projects, beads in each.

### Phase 8: Documentation

- **README.md** — update workspace model section: multi-project, per-bead worktrees, eliminate `BEADS_TARGET_REPO`.
- **AGENTS.md** — update "Current Architecture" and "Learned Workspace Facts" for multi-project model.
- **docs/dashboard.md** — note per-project bead data in dashboard.
- **docs/deployment-compose.md** — remove `BEADS_TARGET_REPO` reference.

## Edge Cases & Risks

| Edge case | Handling |
|---|---|
| Bootstrap (no remote) | Project dir `git init`-ed on creation. Worktrees work from local repo. First beads create content; a "create remote" bead can establish the remote later. Push/PR steps skip gracefully when no remote exists. |
| Concurrent webhooks for same repo | Per-project lock in webhook handler around clone/sync. Second webhook waits for first to finish ensuring workspace. |
| Stale worktree (bead crashed) | `create_bead_worktree` removes stale worktree before creating new one (`git worktree remove --force` + rmtree fallback). |
| Worktree path inside project repo shows as untracked | `.worktrees/` added to `.git/info/exclude` (local-only, not committed). |
| `plan_docs/` not visible in worktree | Plan must be committed to the repo before beads run. Skills updated to commit. |
| Migration from existing single-project setup | Document manual migration: move `/workspace/.beads/` into `/workspace/<slug>/.beads/`. No automatic migration (early-stage project). |
| Per-project SQLite DB | Eliminates shared-DB contention. Each project's `br` commands use its own `BD_DB`. |
| `BEADS_WORKSPACE_ROOT` rename | Optional rename to `workspace_base` for clarity. Keep env var name for backward compat if renaming. |

## Foundation for Parallel Execution (Future Plan)

This plan is a prerequisite for parallel bead execution. After implementation:
- Each bead has an isolated worktree → no file/git collision.
- Each project has its own SQLite DB → no shared-DB contention (though concurrent writes to the same project DB still need SQLite busy-timeout).
- BeadsLoop state is keyed by project → foundation for per-project concurrency control.

Parallel execution plan (separate, future): add `BEADS_MAX_CONCURRENT` setting, thread pool for bead processing, SQLite busy-timeout configuration, bvr/br selection of N distinct non-active beads.

## Validation Plan

1. `pwsh -NoProfile -File ./scripts/validate.ps1 -All` (lint, scan, test) — must pass clean.
2. New multi-project tests pass: project discovery, worktree creation/removal, webhook slug derivation, multi-project BeadsLoop.
3. Manual: create two projects, trigger beads in each, verify isolation (separate worktrees, separate branches, no cross-contamination).
4. Manual: webhook for an existing repo creates project workspace + dispatches to it.
5. Manual: `/perfect-idea` in a new project workspace → `/plan-to-beads` → BeadsLoop discovers and processes.
6. CI green: `gh run list --workflow=validate.yml --limit 5`.

## Affected Files

| File | Change |
|---|---|
| `webhook_receiver/workspace.py` | Major refactor: multi-project + worktree functions |
| `webhook_receiver/config.py` | Remove `beads_target_repo` |
| `webhook_receiver/beads_loop.py` | Multi-project scan loop; per-project state |
| `webhook_receiver/app.py` | Webhook handler: derive slug, ensure workspace |
| `webhook_receiver/runner.py` | Log/event traceability with project slug |
| `webhook_receiver/bead_context.py` | Update tooling reference note for worktree context |
| `webhook_receiver/dashboard.py` | Per-project bead data in dashboard API |
| `compose.yaml` | Remove `BEADS_TARGET_REPO` |
| `compose.development.yaml` | Remove `BEADS_TARGET_REPO` |
| `scripts/prompt.ps1` | Add `-Project` parameter |
| `scripts/attach.ps1` | Add `-Project` parameter |
| `image/.opencode/skills/perfect-idea/SKILL.md` | Commit plan to repo |
| `image/.opencode/skills/plan-to-beads/SKILL.md` | Project context note |
| `tests/test_workspace.py` | Worktree + multi-project tests |
| `tests/test_beads_loop.py` | Multi-project BeadsLoop tests |
| `tests/test_config.py` | Remove BEADS_TARGET_REPO |
| `tests/test_app.py` | Webhook project derivation |
| `tests/test_runner.py` | Project workspace dispatch |
| `tests/test_dashboard.py` | Per-project dashboard data |
| `tests/test_integration_beads.py` | Multi-project integration |
| `tests/test_integration_webhook.py` | Webhook workspace creation |
| `README.md` | Workspace model docs |
| `AGENTS.md` | Architecture facts update |
| `docs/dashboard.md` | Per-project note |
| `docs/deployment-compose.md` | Remove BEADS_TARGET_REPO |
