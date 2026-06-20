# Orchestrator Agent Loop Refactor — Architecture Guide

## Executive Summary

This document details the architecture for migrating the `orchestrator-service` from a markdown-driven workflow to a Beads-backed three-tier software factory. It corrects inaccuracies in the original refactor plans, defines the data flow, and specifies implementation details for each component.

## Three-Tier Pipeline

| Phase | Actor | Input | Action | Output |
|-------|-------|-------|--------|--------|
| **1. Ideation** | `/perfect-idea` (PM Agent skill) | A loose human idea | Interrogates constraints & architecture via conversation | `application_plan.md` |
| **2. Planning** | `/plan-to-beads` (Scrum Agent skill) | `application_plan.md` | Derives Epics/Tasks/ACs and maps DAG dependencies | `.beads/` Graph DAG |
| **3. Execution** | `BeadsLoop` (background thread) | `.beads/` Graph DAG | Spawns isolated agents per bead to write code, test, and close beads | Working Software + PRs |

## Corrections from Original Plans

The original plans (`docs/agent-loop-dev-plans/agent-loop-dev-plan.md` and `Detailed Agent Loop Dev Plan (v2).md`) contain several inaccuracies that conflict with the current codebase. **Existing code trumps plan docs.**

### 1. `bvr --robot-ready --json` does not exist

**Original plan**: `bvr --robot-ready --json` to query unblocked tasks.

**Correction**: The `bvr` binary has no `--robot-ready` flag. The correct commands are:
- `br ready --json` — returns all open, unblocked tasks (from `beads_rust`)
- `bvr --robot-next` — returns the single highest-priority task (from `beads_viewer_rust`)
- `bvr --robot-triage` — returns full triage payload with ranking

**Implementation**: Use `br ready --json` in `beads_loop.py` to get the list of ready tasks, then select by priority.

### 2. Port 4096 vs 4099

**Original plan** (`plan_docs/plan.md`): Server listens on port `4096`.

**Correction**: The actual code uses port `4099` everywhere:
- `compose.yaml`: `4099:4099`
- `Dockerfile`: `EXPOSE 4099`, `CMD ["opencode", "serve", "--port", "4099"]`
- `scripts/prompt.ps1`: default port `4099`
- `webhook_receiver/config.py`: default `http://localhost:4099`

**Implementation**: Use `4099` in all new code.

### 3. Synchronous dispatch model

**Original plan**: `runner.py` refactored to execute webhook prompt synchronously, then trigger `run_loop()`.

**Correction**: The current `runner.py` uses `BackgroundTasks` (fire-and-forget). Making it synchronous would block the HTTP handler for 5-60+ minutes per agent session. This is architecturally wrong for a webhook receiver.

**Implementation**: 
- `runner.py` stays unchanged (fire-and-forget, returns 202 immediately)
- `BeadsLoop` runs as a separate daemon thread started in `__main__.py`
- The loop polls `br ready --json` periodically and dispatches agents independently
- Per-workspace locking prevents duplicate loops for the same repo

### 4. Skills path

**Original plan**: `image/.agents/skills/plan-to-beads/SKILL.md`

**Correction**: The repo has `image/.opencode/commands/` (20 commands) and `image/.opencode/agents/` (15 agents). There is no `.agents/skills/` directory. However, the user explicitly chose to create `image/.agents/skills/` as a new directory for agent skills (auto-discovered by task description matching).

**Implementation**: Create `image/.agents/skills/perfect-idea/SKILL.md` and `image/.agents/skills/plan-to-beads/SKILL.md`.

### 5. Legacy files to delete

**Original plan**: Delete `plan_docs/epic-implementation-workflow.md`, `plan_docs/epic-planning-workflow.md`, `plan_docs/phase-epic-plan-template.md`, `plan_docs/epic-plan-and-implmentation-workflow.md`.

**Correction**: These files do not exist in the current repo. Nothing to delete.

### 6. `workspace_manager.py` git worktrees

**Original plan**: Use `git worktree add` for per-bead isolation outside the main repo tree.

**Correction**: Git worktrees add complexity (nested repos, worktree management). A simpler approach is per-bead clone directories.

**Implementation**: 
- Clone target repo into `/workspace/<bead_id>/`
- Create branch `task/<bead_id>` in the clone
- Pass `-Workspace /workspace/<bead_id>/` to `prompt.ps1`
- On success: push branch, create PR via `gh pr create`
- On failure: delete directory, retry from fresh clone

### 7. `_prompt_script_invocation` signature

**Original plan**: Calls `_prompt_script_invocation(settings, prompt_path)` directly.

**Correction**: The current `runner.py` function signature is correct. `beads_loop.py` should import and reuse `_prompt_script_invocation` and `_stream_to_logger_and_file` from `runner.py` without modification.

---

## Component Architecture

### BeadsLoop (`webhook_receiver/beads_loop.py`)

```
┌─────────────────────────────────────────────────────────┐
│                    BeadsLoop Thread                      │
│                                                         │
│  while running:                                         │
│    for workspace in active_workspaces:                  │
│      ready_beads = br_ready_json(workspace)             │
│      for bead in ready_beads:                           │
│        if workspace in active_loops: skip               │
│        with lock:                                       │
│          active_loops.add(workspace)                    │
│        try:                                             │
│          ws = create_workspace(bead_id, repo_url)       │
│          spawn_agent(settings, bead, ws)                │
│          verify_bead_closed(bead_id, ws)                │
│          on_success: push_branch, create_pr, cleanup    │
│        except:                                          │
│          retry_state[bead_id] += 1                      │
│          if retries >= max: halt_for_human              │
│        finally:                                         │
│          active_loops.remove(workspace)                 │
│    sleep(poll_interval)                                 │
└─────────────────────────────────────────────────────────┘
```

**Key behaviors:**
- Polls `br ready --json` every `beads_poll_interval` seconds (default: 10)
- Per-workspace lock prevents concurrent agent spawns for same repo
- Retries up to `beads_max_retries` (default: 3) with error context injected into prompt
- Verifies bead closure via `br show <id> --json` after agent exits
- On success: pushes branch, creates PR via `gh pr create`, cleans workspace
- On final failure: logs error, leaves bead open for human intervention

**Workspace management** (`webhook_receiver/workspace.py`):
- `create_workspace(bead_id, repo_url)` → clones repo into `/workspace/<bead_id>/`, creates branch `task/<bead_id>`, returns path
- `cleanup_workspace(bead_id, success)` → on success: push branch; on failure: delete directory
- `push_branch_and_create_pr(bead_id, workspace_path)` → `git push origin task/<bead_id>`, `gh pr create`

### Agent Spawning

The loop reuses `runner.py`'s `_prompt_script_invocation` and `_stream_to_logger_and_file`:

```python
from webhook_receiver.runner import _prompt_script_invocation, _stream_to_logger_and_file

def spawn_agent(settings: Settings, bead: dict, workspace_path: str, retry_count: int, previous_logs: str = "") -> tuple[bool, str]:
    bead_id = bead['id']
    title = bead['title']
    description = bead.get('description', '')
    
    prompt = f"You have been assigned Bead {bead_id}: {title}.\n\nContext:\n{description}\n"
    if previous_logs:
        prompt += f"\n\nWARNING: Previous attempt failed. Review logs:\n{previous_logs}\n\nFix the code, ensure tests pass, and run `br close {bead_id}`."
    else:
        prompt += f"\n\nWhen completed and ALL tests pass, you MUST run: `br close {bead_id}`."
    
    # Write prompt to temp file
    # Create modified settings with workspace=workspace_path
    # Call _prompt_script_invocation(modified_settings, prompt_path)
    # Spawn Popen, stream stdout/stderr
    # Wait for completion
    # Return (success, logs)
```

### Workspace Isolation

```
/workspace/                    (shared Docker volume)
├─ <bead_id_1>/               (per-bead clone)
│  ├─ .git/
│  ├─ src/
│  ├─ tests/
│  └─ .beads/
├─ <bead_id_2>/
│  └─ ...
└─ <bead_id_3>/
   └─ ...
```

Each bead gets:
1. Fresh clone of target repo
2. Branch `task/<bead_id>` created from `main`
3. `-Workspace /workspace/<bead_id>/` passed to `prompt.ps1`
4. Agent operates in isolated directory (no conflicts with other beads)
5. On completion: branch pushed, PR created, directory cleaned

### Configuration (`webhook_receiver/config.py`)

Add to `Settings` dataclass:

```python
beads_poll_interval: int          # seconds between polls (default: 10)
beads_max_retries: int            # max retries per bead (default: 3)
beads_workspace_root: str         # root dir for per-bead clones (default: "/workspace")
beads_enabled: bool               # enable/disable BeadsLoop (default: True)
```

Environment variables:
- `BEADS_POLL_INTERVAL` (default: `10`)
- `BEADS_MAX_RETRIES` (default: `3`)
- `BEADS_WORKSPACE_ROOT` (default: `/workspace`)
- `BEADS_ENABLED` (default: `true`)

### Startup (`webhook_receiver/__main__.py`)

```python
import threading
from webhook_receiver.beads_loop import BeadsLoop

def main():
    settings = Settings.from_env()
    app = create_app(settings)
    
    if settings.beads_enabled:
        loop = BeadsLoop(settings)
        thread = threading.Thread(target=loop.run, daemon=True)
        thread.start()
        logger.info("BeadsLoop started (poll_interval=%ds)", settings.beads_poll_interval)
    
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level)
```

---

## Data Flow

### Trigger: `/perfect-idea` skill

```
User comments on issue: "/perfect-idea Build a webhook receiver..."
  ↓
GitHub webhook → webhook-receiver → orchestrator agent
  ↓
Orchestrator agent loads /perfect-idea skill
  ↓
Skill interrogates user via conversation (multi-turn)
  ↓
Skill generates plan_docs/application_plan.md
  ↓
User reviews and edits application_plan.md
  ↓
User comments: "/plan-to-beads"
```

### Trigger: `/plan-to-beads` skill

```
User comments on issue: "/plan-to-beads"
  ↓
GitHub webhook → webhook-receiver → orchestrator agent
  ↓
Orchestrator agent loads /plan-to-beads skill
  ↓
Skill reads application_plan.md
  ↓
Skill generates bash script:
  br init
  TASK_1=$(br create "Task 1" --type task --priority 1 | grep -oP 'br-[a-f0-9]+')
  TASK_2=$(br create "Task 2" --type task --priority 2 | grep -oP 'br-[a-f0-9]+')
  br dep add $TASK_2 $TASK_1
  br sync --flush-only
  ↓
Skill executes bash script
  ↓
.beads/ directory created with DAG
  ↓
Skill commits .beads/ to repo
```

### Execution: BeadsLoop

```
BeadsLoop thread (running in webhook-receiver)
  ↓
Polls: br ready --json (in /workspace or target repo)
  ↓
Returns: [{"id": "br-abc123", "title": "Task 1", "priority": 1, "description": "..."}]
  ↓
For each ready bead:
  ↓
  create_workspace("br-abc123", "https://github.com/org/repo")
    → git clone into /workspace/br-abc123/
    → git checkout -b task/br-abc123
  ↓
  spawn_agent(settings, bead, "/workspace/br-abc123/")
    → write prompt to /tmp/orchestrator-webhook/bead-br-abc123-<uuid>.md
    → pwsh -NoProfile -File /app/scripts/prompt.ps1 -ServerUrl http://orchestratorservice:4099 -Workspace /workspace/br-abc123/ -PromptFile /tmp/...
    → opencode run --attach http://orchestratorservice:4099 --dir /workspace/br-abc123/ --agent orchestrator "..."
    → stream stdout/stderr to log files
    → wait for completion
  ↓
  verify_bead_closed("br-abc123", "/workspace/br-abc123/")
    → br show br-abc123 --json
    → check status == "closed"
  ↓
  If closed (success):
    → git push origin task/br-abc123
    → gh pr create --title "Implement br-abc123: Task 1" --body "..."
    → cleanup_workspace("br-abc123", success=True)
  ↓
  If still open (failure):
    → retry_state["br-abc123"] += 1
    → if retries < max:
        → read stderr logs
        → spawn_agent again with error context injected
    → else:
        → log error, leave bead open for human intervention
        → cleanup_workspace("br-abc123", success=False)
  ↓
  Sleep(poll_interval)
  ↓
  Poll again: br ready --json
    → next unblocked bead (TASK_2 now unblocked since TASK_1 closed)
    → repeat
```

---

## Beads CLI Reference

### `br` (beads_rust)

| Command | Purpose | Example |
|---------|---------|---------|
| `br init` | Initialize `.beads/` workspace | `br init` |
| `br create` | Create a bead (task/epic/bug) | `br create "Title" --type task --priority 1 --description "..."` |
| `br dep add` | Add dependency (child blocked by parent) | `br dep add br-child br-parent` |
| `br close` | Close a bead (mark complete) | `br close br-abc123 --reason "Done"` |
| `br ready` | List open, unblocked beads | `br ready --json` |
| `br show` | Show bead details | `br show br-abc123 --json` |
| `br sync --flush-only` | Export JSONL (idempotent) | `br sync --flush-only` |
| `br update` | Update bead metadata | `br update br-abc123 --status in_progress` |
| `br list` | List beads with filters | `br list --status open --json` |

### `bvr` (beads_viewer_rust)

| Command | Purpose | Example |
|---------|---------|---------|
| `bvr --robot-next` | Single best next task | `bvr --robot-next --format json` |
| `bvr --robot-triage` | Full triage payload | `bvr --robot-triage --format json` |
| `bvr --robot-plan` | Execution plan with parallel tracks | `bvr --robot-plan --format json` |
| `bvr --robot-overview` | Compact project snapshot | `bvr --robot-overview` |
| `bvr --robot-insights` | Graph metrics and cycles | `bvr --robot-insights` |

**Note**: `bvr --robot-ready` does NOT exist. Use `br ready --json` instead.

---

## Dockerfile.webhook Changes

```dockerfile
# --- Stage 1: Rust Builder ---
FROM rust:1.77-slim AS rust-builder
WORKDIR /build
RUN apt-get update && apt-get install -y git pkg-config libssl-dev
RUN cargo install --git https://github.com/Dicklesworthstone/beads_rust.git beads_rust
RUN cargo install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git bvr

# --- Stage 2: Final Python/UV Image ---
FROM debian:trixie-20260518-slim

# ... [existing apt-get, PowerShell, uv, opencode installs] ...

# Copy compiled Beads binaries from builder
COPY --from=rust-builder /usr/local/cargo/bin/br /usr/local/bin/br
COPY --from=rust-builder /usr/local/cargo/bin/bvr /usr/local/bin/bvr

# ... [existing COPY, uv sync, ENV] ...
```

---

## Task Completion Contract

Append to `image/local_ai_instruction_modules/ai-development-instructions.md`:

```markdown
## TASK COMPLETION CONTRACT (CRITICAL)

You are a localized worker operating within a strict graph-based execution loop.
You do NOT have authority over the broader project plan.

When you have completed your assigned task, and ALL local tests pass, you must follow this exact sequence:

1. Commit your code: `/safe-commit`
2. Mark the graph node complete: `br close <YOUR_ASSIGNED_BEAD_ID>`
3. Exit the environment cleanly.

Failure to execute `br close` will result in infinite retry loops and task failure. Do NOT attempt to complete blocked tasks.
```

---

## Coexistence with Existing System

The current label-driven orchestration system remains unchanged:

- `orchestration_prompt.jinja2.md` — match-case branching logic preserved
- Label triggers: `orchestration:plan-approved`, `orchestration:epic-ready`, `orchestration:epic-complete`, etc.
- Webhook receiver continues to dispatch via `runner.py` (fire-and-forget)

The Beads pipeline is **additive**:
- Triggered by user-initiated skill commands (`/perfect-idea`, `/plan-to-beads`)
- `BeadsLoop` runs independently as a background thread
- Both systems can operate concurrently without interference

---

## Maestro/Supervisor (Out of Scope)

The maestro/supervisor architecture (dual OpenCode servers with leapfrog recovery) is explicitly **out of scope** for this refactor. See `plan_docs/maestro_architecture_options.md` and `plan_docs/orchestration_supervisor.md` for future consideration.

This refactor focuses on the three-tier pipeline (Ideation → Planning → Execution) without automated recovery from orchestration failures. The `BeadsLoop` provides basic retry logic (up to 3 attempts with error context), but does not implement the full maestro control plane (status reporting, directive polling, hop counting).

---

## Validation Plan

1. **Build Docker image**: `docker compose build webhook-receiver`
2. **Verify binaries**: `docker compose run --rm webhook-receiver br --version && bvr --robot-help`
3. **Start stack**: `docker compose up`
4. **Trigger `/perfect-idea`**: Use webhook simulator or GitHub issue comment
5. **Verify application plan**: Check `plan_docs/application_plan.md` generated
6. **Trigger `/plan-to-beads`**: Comment on same issue
7. **Verify beads created**: `docker compose exec webhook-receiver br list --json`
8. **Verify loop drains**: Watch logs `docker compose logs -f webhook-receiver` for "BeadsLoop" messages
9. **Verify PR created**: Check GitHub for PR from `task/<bead_id>` branch
10. **Run validation**: `pwsh -NoProfile -File ./scripts/validate.ps1 -All`
