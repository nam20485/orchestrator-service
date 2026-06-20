---
name: plan-to-beads
description: "Converts a high-level human-readable Application Plan (Markdown) into a strict, machine-readable Directed Acyclic Graph (DAG) using the `br` (beads) CLI tool. Packs Acceptance Criteria and Validation steps into Bead descriptions, and defines blocking dependencies between tasks."
---

<objective>
Translate an application plan into a graph of atomic execution tasks inside the `.beads/` directory. Pack the Acceptance Criteria into the Bead descriptions, and define strict blocking dependencies between tasks so the execution loop runs them in the correct order.
</objective>

<inputs>
- `$plan_doc`: Path to the high-level application plan (e.g., `plan_docs/application_plan.md`). If not provided explicitly, look for `plan_docs/application_plan.md`.
</inputs>

<prerequisites>
The `br` CLI must be installed. Verify with `br --version`. If missing, install via:
```
cargo install --git https://github.com/Dicklesworthstone/beads_rust.git beads_rust
```
</prerequisites>

<instructions>
You are an expert Technical Project Manager. Your job is to convert the provided Markdown plan into a `beads` execution graph.

Instead of writing a phase document, you will write and execute a single bash script containing `br` CLI commands.

### Step 1: Read and Parse the Plan

1. Read `$plan_doc` (default: `plan_docs/application_plan.md`).
2. Extract the Implementation Plan section with all phases, epics, and tasks.
3. For each task, extract:
   - **Title**: A concise name for the task.
   - **Context**: Why this task exists and what it does.
   - **Acceptance Criteria**: Bullet points defining "done".
   - **Validation**: Commands to run to verify (e.g., `uv run pytest tests/`).
   - **Priority**: Execution order (1 = highest, increasing numbers = lower priority).
   - **Dependencies**: Which other tasks must complete first.

### Step 2: Initialize Beads

```bash
br init
```

### Step 3: Create Nodes and Pack Descriptions

For every Task in the plan, use `br create` and pass a heredoc to the `--description` flag. The description must contain the Context, Acceptance Criteria, and Validation instructions so the execution agent has full context.

Assign priorities (`--priority 1`, `--priority 2`, etc.) based on execution order. Save returned IDs to bash variables so you can link them.

**Important**: Use `--type task` for individual tasks and `--type epic` for phase rollups.

### Step 4: Define Dependencies

Use `br dep add <BLOCKED_BEAD> <BLOCKING_BEAD>` to map the exact order of execution. The first argument is the bead that is BLOCKED; the second is the bead that BLOCKS it.

### Step 5: Execute and Commit

Run the generated bash script. Once complete, run a final export and commit:

```bash
br sync --flush-only
git add .beads/
git commit -m "Add beads DAG from application plan"
```

Once the script completes, the orchestrator's BeadsLoop background thread will automatically detect the unblocked tasks and begin executing them.
</instructions>

<example_script>
```bash
#!/bin/bash
br init

# Phase rollup epics
EPIC_FOUNDATION=$(br create "Phase 1: Foundation Setup" --type epic --priority 1 | grep -oP 'br-[a-f0-9]+')

# Task with packed description
TASK_DB_DESC=$(cat << 'EOF'
Context: We need a PostgreSQL schema for user data before the API layer can function.
Acceptance Criteria:
1. Create users table with id, email, created_at columns
2. Add alembic migration
3. Migration applies cleanly on a fresh database
Validation: Run `uv run alembic upgrade head` then `uv run pytest tests/test_db.py -v`
EOF
)
TASK_DB=$(br create "Configure PostgreSQL Schema" --description "$TASK_DB_DESC" --type task --priority 1 | grep -oP 'br-[a-f0-9]+')

TASK_API_DESC=$(cat << 'EOF'
Context: Scaffold the FastAPI endpoints that serve user data from the database.
Acceptance Criteria:
1. GET /users returns list of users
2. POST /users creates a new user
3. Endpoints are covered by integration tests
Validation: Run `uv run pytest tests/test_api.py -v`
EOF
)
TASK_API=$(br create "Scaffold FastAPI Endpoints" --description "$TASK_API_DESC" --type task --priority 2 | grep -oP 'br-[a-f0-9]+')

# The Epic requires both tasks to finish
br dep add $EPIC_FOUNDATION $TASK_DB
br dep add $EPIC_FOUNDATION $TASK_API

# The API is blocked by the DB schema
br dep add $TASK_API $TASK_DB

# Sync graph to disk safely
br sync --flush-only
```
</example_script>

<cli_reference>
Key `br` commands for this skill:
- `br init` — Initialize `.beads/` workspace
- `br create "Title" --type task --priority 1 --description "..."` — Create a bead
- `br dep add <blocked_id> <blocking_id>` — Add dependency
- `br sync --flush-only` — Idempotent JSONL export before git commit
- `br ready --json` — List unblocked tasks (used by execution loop)

Bead IDs are printed as `br-<hex>` (e.g., `br-a1b2c3`). Capture them with `| grep -oP 'br-[a-f0-9]+'`.
</cli_reference>
