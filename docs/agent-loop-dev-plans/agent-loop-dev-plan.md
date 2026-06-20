# Detailed Agent Loop Development Plan (Agent Handoff Document)

> **TARGET AGENT INSTRUCTIONS:** You are tasked with a critical architectural refactor. You will migrate the `orchestrator-service` from a probabilistic, procedural markdown-driven workflow to a deterministic, graph-backed state machine utilizing the Beads ecosystem (`br` and `bvr`).

This document serves as your single source of truth. It contains the conceptual architectural design, the step-by-step implementation plan, and the exact code blocks required for the new files. It is heavily context-aware of the existing `orchestrator-service` codebase and leverages existing internal tools (like `scripts/prompt.ps1` and `runner.py`'s streaming logic) to minimize redundancy.

## Executive Summary & Architectural Shift

The current iteration of the orchestration system relies on a Large Language Model (LLM) to parse, update, and maintain its place within massive Markdown files (e.g., `phase-epic-plan-template.md`). This approach suffers from context-window degradation and limits the ability to orchestrate complex, multi-step dependencies reliably.

By transitioning to a Beads-backed Graph State Machine, we completely remove state management from the LLM's context window.

- **Atomic State**: Tasks become atomic JSON objects managed in a Directed Acyclic Graph (DAG) inside a local `.beads/` directory.
- **The Ralph Loop**: The orchestrator becomes an infinite, resilient loop (`ralph_loop.py`) that queries the graph for unblocked work via the `bvr --robot-ready` CLI command.
- **Project Isolation**: We do not need complex Docker-in-Docker or Git Worktree gymnastics. Project isolation is naturally achieved by passing the target repository path to the existing `scripts/prompt.ps1` via the `-Workspace` argument (mapped to `settings.workspace`). The OpenCode container natively scopes its environment to this directory.
- **Synchronous Webhook Dispatch**: The webhook receiver's `runner.py` is refactored to execute the immediate incoming webhook prompt synchronously, and then trigger the graph loop to relentlessly drain any unblocked beads.

## Phase 1: Infrastructure & Toolchain Preparation

The orchestrator requires reliable access to the `br` (database editor) and `bvr` (graph analysis) Rust binaries. This applies to both the local Windows/PowerShell development environment and the Linux-based containerized webhook receiver.

### 1.1 Update Local Development Setup

**Target File:** `scripts/install-dev-tools.ps1`

We must ensure the local machine compiles the Beads ecosystem so developers can seed initial plans and test the graph. Place this logic inside the existing `try` block, right before the final success message:

```powershell
# Verify Rust is installed before attempting to compile
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Warning 'Cargo is not installed. Please install Rust via <https://rustup.rs/> to compile the Beads ecosystem.'
} else {
    Write-Host 'Compiling and installing Beads ecosystem (this may take a few minutes)...' -ForegroundColor Cyan

    # br: The core tracker (create, close, dep add)
    cargo install --git https://github.com/Dicklesworthstone/beads_rust.git --tag v0.2.15 beads_rust

    # bvr: The analytical engine (graph resolution, --robot-ready)
    cargo install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git

    Write-Host 'Beads ecosystem installed successfully.' -ForegroundColor Green
}
```

### 1.2 Update Webhook Container (Multi-Stage Build)

**Target File:** `Dockerfile.webhook`

To keep the final production Docker image lean, we must implement a multi-stage build. We will compile `br` and `bvr` in a heavyweight Rust builder stage, then copy ONLY the compiled binaries into the final lightweight Python image.

```dockerfile
# --- Stage 1: Rust Builder ---
FROM rust:1.77-slim AS rust-builder
WORKDIR /build
RUN apt-get update && apt-get install -y git pkg-config libssl-dev
RUN cargo install --git https://github.com/Dicklesworthstone/beads_rust.git --tag v0.2.15 beads_rust
RUN cargo install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git

# --- Stage 2: Final Python/UV Image ---
FROM debian:trixie-20260518-slim

# ... [Keep your existing apt-get and powershell installations here]

# Copy compiled Beads binaries from the builder stage into the final image
COPY --from=rust-builder /usr/local/cargo/bin/br /usr/local/bin/br
COPY --from=rust-builder /usr/local/cargo/bin/bvr /usr/local/bin/bvr

# ... [Keep your existing uv sync and ENV setup here]
```

## Phase 2: Deprecating Markdown State & Adding the Planner Skill

We must eliminate the generation of human-readable Markdown tracking files and replace them with atomic, machine-readable graph commands.

### 2.1 Clean Up Old Workflows

Permanently delete the following legacy Markdown workflow templates. They are artifacts of the previous workflow system and will actively confuse future agents if left in the repository context:

- `plan_docs/epic-implementation-workflow.md`
- `plan_docs/epic-planning-workflow.md`
- `plan_docs/phase-epic-plan-template.md`
- `plan_docs/epic-plan-and-implmentation-workflow.md`

### 2.2 Create the plan-to-beads Skill

**Target File:** `image/.agents/skills/plan-to-beads/SKILL.md` (Create new file and necessary directories)

This skill overrides the default planning behavior. It forces the LLM to convert a Markdown plan into a highly deterministic Bash script using `br create` and `br dep add` commands.

````markdown
---
name: plan-to-beads
description: "Converts a high-level human-readable Application Plan (Markdown) into a strict, machine-readable Directed Acyclic Graph (DAG) using the `br` (beads) CLI tool."
---

<objective>
Translate an application plan into a graph of atomic execution tasks inside the `.beads/` directory. Define strict blocking dependencies between tasks to ensure the execution loop runs them in the correct mathematical order.
</objective>

<inputs>
- `$plan_doc`: Path to the high-level application plan (e.g., `plan_docs/plan.md`)
</inputs>

<instructions>
You are an expert Technical Project Manager. Your job is to convert the provided Markdown plan into a `beads` execution graph.

Instead of writing a phase document, you will write and execute a single bash script containing `br` CLI commands.

## Instructions

1. **Initialize Beads:** Start the script with `br init`.
2. **Create Nodes:** For every Phase, Epic, and Task in the plan, use `br create`.
   - Assign priorities based on execution order (`--priority 1`, `--priority 2`, etc.).
   - Save the returned Bead IDs to bash variables so you can link them.
3. **Define Dependencies:** Use `br dep add <BLOCKED_BEAD> <BLOCKING_BEAD>` to map the exact order of execution.
4. **Execute & Commit:** Run the generated bash script. Once complete, run `git add .beads/` and commit the new graph state to the repository.

## Bash Script Template Example

(Note: Ensure your script follows this variable-capture pattern)

```bash
#!/bin/bash
br init

EPIC_FOUNDATION=$(br create "Phase 1: Foundation Setup" --type epic --priority 1 | grep -oP 'br-[a-f0-9]+')
TASK_DB=$(br create "Configure PostgreSQL Schema" --type task --priority 1 | grep -oP 'br-[a-f0-9]+')
TASK_API=$(br create "Scaffold FastAPI Endpoints" --type task --priority 2 | grep -oP 'br-[a-f0-9]+')

# The Epic requires both tasks to finish
br dep add $EPIC_FOUNDATION $TASK_DB
br dep add $EPIC_FOUNDATION $TASK_API

# The API is blocked by the DB schema
br dep add $TASK_API $TASK_DB

# Sync graph to disk safely
br sync --flush-only
```

Once the script completes, the orchestrator loop will automatically detect the unblocked tasks and begin executing them.
</instructions>
````

## Phase 3: Implementing the Graph State Machine (ralph_loop.py)

This new loop daemon queries the graph, evaluates task priority, and seamlessly injects execution prompts into the running OpenCode service. Crucially, it reuses the robust subprocess and streaming execution logic already built into `runner.py`.

**Target File:** `webhook_receiver/ralph_loop.py` (Create new file)

```python
import subprocess
import json
import time
import logging
import os
import tempfile
import threading
from pathlib import Path

from webhook_receiver.config import Settings

# Re-use the robust execution logic already built in runner.py
from webhook_receiver.runner import _prompt_script_invocation, _stream_to_logger_and_file

logger = logging.getLogger(__name__)


def get_ready_beads(repo_path: str) -> list:
    """Queries bvr for all unblocked tasks in the specific workspace."""
    try:
        result = subprocess.run(
            ["bvr", "--robot-ready", "--json"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"Failed to query bvr in {repo_path}: {e}")
        return []


def evaluate_graph_state(beads: list) -> dict | None:
    """Selects the highest priority unblocked task."""
    if not beads:
        return None
    # Lower numbers indicate higher priority execution
    return sorted(beads, key=lambda b: b.get('priority', 999))[0]


def check_bead_status(repo_path: str, bead_id: str) -> str:
    """The Ultimate Source of Truth: checks if the bead was actually closed via br."""
    try:
        result = subprocess.run(
            ["br", "status", bead_id],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        return result.stdout.strip().lower()
    except subprocess.CalledProcessError:
        return "unknown"


def spawn_agent(settings: Settings, bead: dict, retry_count: int, previous_logs: str = "") -> tuple[bool, str]:
    """Injects a task prompt into the running orchestrator using the standard prompt.ps1 script."""
    bead_id = bead.get('id')
    title = bead.get('title')
    description = bead.get('description', '')
    repo_path = settings.workspace

    prompt = f"You have been assigned Bead {bead_id}: {title}.\n\nContext:\n{description}\n"

    if previous_logs:
        prompt += f"\n\n🛑 WARNING: Your previous attempt failed. Review logs: 🛑\n{previous_logs}\n\nFix the code, ensure tests pass, and run `br close {bead_id}`."
    else:
        prompt += f"\n\nWhen completed and ALL tests pass, you MUST run: `br close {bead_id}`."

    logger.info(f"Injecting prompt for {bead_id} into service (Attempt {retry_count + 1}).")

    log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
    log_dir.mkdir(parents=True, exist_ok=True)

    fd, prompt_name = tempfile.mkstemp(prefix=f"bead-{bead_id}-", suffix=".md", dir=log_dir)
    prompt_path = Path(prompt_name)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    # Generate the pwsh command using your existing config settings
    cmd = _prompt_script_invocation(settings, prompt_path)

    stdout_path = log_dir / f"{prompt_path.stem}.stdout"
    stderr_path = log_dir / f"{prompt_path.stem}.stderr"
    stdout_file = open(stdout_path, "w", encoding="utf-8")
    stderr_file = open(stderr_path, "w", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )

        # Stream output using the logic from runner.py to maintain consistent logging
        t1 = threading.Thread(target=_stream_to_logger_and_file, args=(proc.stdout, stdout_file, f"bead-{bead_id}"), daemon=True)
        t2 = threading.Thread(target=_stream_to_logger_and_file, args=(proc.stderr, stderr_file, f"bead-{bead_id}-err"), daemon=True)
        t1.start()
        t2.start()

        proc.wait()  # Wait for the agent to finish
        t1.join()
        t2.join()

        # Verify the agent actually closed the task in the graph
        current_status = check_bead_status(repo_path, bead_id)
        if "open" in current_status or current_status == "unknown":
            logger.warning(f"Bead {bead_id} is still open. Agent failed its contract.")
            with open(stderr_path, "r", encoding="utf-8") as f:
                err_logs = f.read()
            return False, err_logs

        return True, ""

    except Exception as e:
        logger.error(f"Error executing prompt script for {bead_id}: {e}")
        return False, str(e)


def run_loop(settings: Settings):
    """
    Finite loop: Runs until the graph is empty or completely blocked.
    """
    repo_path = settings.workspace
    logger.info(f"Waking up Ralph Loop for workspace: {repo_path}")
    retry_state = {}
    MAX_RETRIES = 3

    while True:
        ready_beads = get_ready_beads(repo_path)
        next_bead = evaluate_graph_state(ready_beads)

        if not next_bead:
            logger.info(f"No ready beads found in {repo_path}. Graph is complete or blocked. Exiting loop.")
            break

        bead_id = next_bead['id']

        if bead_id not in retry_state:
            retry_state[bead_id] = {'count': 0, 'logs': ""}

        current_retries = retry_state[bead_id]['count']

        # Circuit breaker to prevent infinite resource drain on impossible tasks
        if current_retries >= MAX_RETRIES:
            logger.error(f"🚨 Bead {bead_id} exceeded max retries. Halting loop to await human intervention.")
            break

        success, logs = spawn_agent(settings, next_bead, current_retries, retry_state[bead_id]['logs'])

        if success:
            logger.info(f"✅ Successfully completed {bead_id}.")
            del retry_state[bead_id]
        else:
            logger.error(f"❌ Agent failed to complete {bead_id}.")
            retry_state[bead_id]['count'] += 1
            # Truncate logs to prevent blowing out the context window on the next attempt
            retry_state[bead_id]['logs'] = logs[-3000:]

        # Brief pause before querying the graph again
        time.sleep(3)
```

## Phase 4: Rewiring the Webhook Receiver (runner.py)

Modify `dispatch_to_opencode` to synchronously execute the incoming GitHub webhook prompt (such as a `/plan-to-beads` command), and then trigger the Ralph Loop to automatically process any resulting beads.

The added `_loop_lock` prevents race conditions where multiple webhooks could spawn duplicate loops for the same repository.

**Target File:** `webhook_receiver/runner.py`

Keep `_base_args`, `_prompt_script_invocation`, and `_stream_to_logger_and_file` exactly as they are. Replace the `dispatch_to_opencode` function and add the lock mechanism at the top of the file:

```python
# Add this import at the top
from webhook_receiver.ralph_loop import run_loop

_active_loops: set[str] = set()
_loop_lock = threading.Lock()

# ... [existing helper functions: _base_args, _prompt_script_invocation, etc.]


def dispatch_to_opencode(settings: Settings, prompt: str) -> None:
    """Run the initial webhook prompt, then seamlessly drain the Beads graph."""
    workspace = settings.workspace

    with _loop_lock:
        if workspace in _active_loops:
            logger.info(f"Agent loop already running for workspace={workspace}. Ignoring trigger.")
            return
        _active_loops.add(workspace)

    try:
        log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
        log_dir.mkdir(parents=True, exist_ok=True)

        fd, prompt_name = tempfile.mkstemp(prefix="prompt-", suffix=".md", dir=log_dir)
        prompt_path = Path(prompt_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)

        cmd = _prompt_script_invocation(settings, prompt_path)

        logger.info(f"Executing webhook prompt for workspace={workspace}")

        stdout_path = log_dir / f"{prompt_path.stem}.stdout"
        stderr_path = log_dir / f"{prompt_path.stem}.stderr"
        stdout_file = open(stdout_path, "w", encoding="utf-8")
        stderr_file = open(stderr_path, "w", encoding="utf-8")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )

        t1 = threading.Thread(target=_stream_to_logger_and_file, args=(proc.stdout, stdout_file, "opencode"), daemon=True)
        t2 = threading.Thread(target=_stream_to_logger_and_file, args=(proc.stderr, stderr_file, "opencode-err"), daemon=True)
        t1.start()
        t2.start()

        # Wait for the initial webhook prompt (e.g., /plan-to-beads) to finish
        proc.wait()
        t1.join()
        t2.join()

        # NOW relentlessly drain the graph
        logger.info(f"Webhook prompt finished. Triggering Ralph Loop for workspace={workspace}")
        run_loop(settings)

    except Exception as e:
        logger.error(f"Fatal error in orchestration for {workspace}: {e}", exc_info=True)
    finally:
        with _loop_lock:
            if workspace in _active_loops:
                _active_loops.remove(workspace)
            logger.info(f"Lock released for workspace={workspace}")
```

## Phase 5: Update AI Development Instructions

We must instill a rigid API contract so the OpenCode execution agents know exactly how to interact with the Beads database when they conclude their work.

**Target File:** `image/local_ai_instruction_modules/ai-development-instructions.md`

Append this block to the bottom of the file:

```markdown
## 🛑 TASK COMPLETION CONTRACT (CRITICAL) 🛑

You are a localized worker operating within a strict graph-based execution loop.
You do NOT have authority over the broader project plan.

When you have completed your assigned task, and ALL local tests pass, you must follow this exact sequence:

1. Commit your code: `/safe-commit`
2. Mark the graph node complete: `br close <YOUR_ASSIGNED_BEAD_ID>`
3. Exit the environment cleanly.

Failure to execute `br close` will result in infinite retry loops and task failure. Do NOT attempt to complete blocked tasks.
```

## Completion Verification

To verify the migration was successful:

1. Rebuild the orchestrator container (`docker compose build`) so the Cargo packages are natively compiled into the final webhook image.
2. Spin up the orchestrator and trigger the system by commenting `/plan-to-beads` on a GitHub issue in a target repository.
3. Watch the logs (`docker compose logs -f webhook`) to verify `app.py` receives the event, `runner.py` executes the initial skill prompt, and `ralph_loop.py` seamlessly takes over to sequentially drain the newly generated `.beads` graph!
