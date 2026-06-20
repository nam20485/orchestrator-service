# Orchestrator Agent Loop Refactor — Complete Implementation (Application Plan)

## Overview

Migrate the `orchestrator-service` from a probabilistic, markdown-driven workflow to a deterministic, three-tier software factory using the Beads ecosystem (`br` and `bvr`). The system will support interactive ideation, graph-based planning, and autonomous task execution via a background loop that drains unblocked beads from a DAG.

This refactors the existing webhook receiver and OpenCode server stack. The current label-driven orchestration prompt (`orchestration_prompt.jinja2.md`) and match-case branching logic are preserved. The Beads pipeline is an additional execution path triggered by user-initiated skill commands (`/perfect-idea`, `/plan-to-beads`).

Supporting docs: `architecture.md` (accompanies this file)

**Target location after implementation**: `plan_docs/agent-loop-refactor/application_plan.md`

## Goals

- Replace LLM-managed markdown state with atomic, graph-backed task nodes
- Enable autonomous, retry-resilient execution of planned work via a background loop
- Provide a three-tier abstraction: Ideation → Planning → Execution
- Preserve the existing webhook-driven, label-based orchestration system (coexist)
- Isolate per-task workspaces to prevent concurrent agent conflicts

## Technology Stack

- Language: Python 3.11+ (webhook receiver), PowerShell 7.6.2 (prompt scripts), Rust (Beads binaries)
- AI/Runtime: OpenCode CLI 1.17.8, Z.AI GLM models, Alibaba Model Studio
- Architecture: Three-tier pipeline (Ideation → Planning → Execution) with Beads DAG
- Databases/Storage: SQLite + JSONL (Beads), shared Docker volume `opencode-workspace`
- Logging/Observability: Python logging, OpenCode server logs, per-bead stdout/stderr capture
- Containerization/Infra: Docker, Compose, multi-stage Rust build for webhook image

## Application Features

- Interactive idea interrogation and formal application plan generation (`/perfect-idea`)
- Deterministic conversion of markdown plans into Beads DAGs (`/plan-to-beads`)
- Background execution loop that autonomously drains unblocked beads (`BeadsLoop`)
- Per-bead workspace isolation with git clone, branch, and PR workflow
- Retry logic with error context injection (up to 3 attempts per bead)
- Task completion contract enforced via `br close`

## System Architecture

### Core Services

1. **webhook-receiver** — FastAPI service that verifies GitHub webhooks, assembles prompts, and dispatches agent runs. Now also hosts the background BeadsLoop thread.
2. **orchestratorservice** — OpenCode server (`opencode serve` on :4099) that executes agent sessions.
3. **webhook-proxy** — Caddy reverse proxy for HTTPS ingress.

### Key Features (system-level)

- Three-tier pipeline: `/perfect-idea` → `/plan-to-beads` → `BeadsLoop`
- Background polling loop (`br ready --json`) with per-workspace locking
- Per-bead workspace isolation: clone repo into `/workspace/<bead_id>/`, create branch, pass `-Workspace` to `prompt.ps1`
- Coexistence with existing label-driven orchestration (no breaking changes)

## Project Structure

```
orchestrator-service/
├─ webhook_receiver/
│  ├─ app.py                    (unchanged)
│  ├─ runner.py                 (unchanged; beads_loop imports helpers)
│  ├─ beads_loop.py             (NEW: background execution loop)
│  ├─ workspace.py              (NEW: per-bead workspace management)
│  ├─ config.py                 (MODIFIED: add beads config fields)
│  └─ __main__.py               (MODIFIED: start beads loop thread)
├─ image/
│  ├─ .agents/skills/
│  │  ├─ perfect-idea/SKILL.md  (NEW)
│  │  └─ plan-to-beads/SKILL.md (NEW)
│  └─ local_ai_instruction_modules/
│     └─ ai-development-instructions.md (MODIFIED: task completion contract)
├─ scripts/
│  └─ install-dev-tools.ps1    (MODIFIED: add br/bvr install)
├─ Dockerfile.webhook           (MODIFIED: multi-stage Rust build)
└─ plan_docs/agent-loop-refactor/
   ├─ application_plan.md       (this file)
   └─ architecture.md           (detailed architecture guide)
```

---

## Implementation Plan

### Phase 1: Infrastructure & Toolchain Preparation

- [ ] 1.1. Update `scripts/install-dev-tools.ps1` to install `br` and `bvr` via `cargo install` (with Rust availability check)
- [ ] 1.2. Update `Dockerfile.webhook` with multi-stage Rust builder to compile `br` and `bvr`, copy binaries into final image
- [ ] 1.3. Create `image/.agents/skills/` directory structure

### Phase 2: Ideation Skill (`/perfect-idea`)

- [ ] 2.1. Create `image/.agents/skills/perfect-idea/SKILL.md` — interactive interrogation skill that produces `application_plan.md`

### Phase 3: Planning Skill (`/plan-to-beads`)

- [ ] 3.1. Create `image/.agents/skills/plan-to-beads/SKILL.md` — converts application plan into `br create` / `br dep add` bash script

### Phase 4: Execution Engine (`beads_loop.py` + `workspace.py`)

- [ ] 4.1. Implement `webhook_receiver/workspace.py` — per-bead workspace management (clone, branch, cleanup, push PR)
- [ ] 4.2. Implement `webhook_receiver/beads_loop.py` — background loop: poll `br ready --json`, spawn agents, verify `br close`, retry logic
- [ ] 4.3. Update `webhook_receiver/config.py` — add `beads_poll_interval`, `beads_max_retries`, `beads_workspace_root` settings
- [ ] 4.4. Update `webhook_receiver/__main__.py` — start BeadsLoop as daemon thread on service startup

### Phase 5: Agent Instructions & Task Contract

- [ ] 5.1. Append task completion contract to `image/local_ai_instruction_modules/ai-development-instructions.md`

### Phase 6: Validation & Testing

- [ ] 6.1. Add unit tests for `workspace.py` (clone, branch, cleanup logic)
- [ ] 6.2. Add unit tests for `beads_loop.py` (poll, spawn, retry, lock logic)
- [ ] 6.3. Run `pwsh -NoProfile -File ./scripts/validate.ps1 -All` and fix all failures
- [ ] 6.4. Integration test: rebuild Docker image, trigger `/plan-to-beads` via webhook simulator, verify loop drains beads

---

## Mandatory Requirements Implementation

### Testing & Quality Assurance

- [ ] Unit tests — coverage target: 80%+ for `beads_loop.py` and `workspace.py`
- [ ] Integration tests — end-to-end webhook → beads → agent → close cycle
- [ ] Automated tests in CI (`validate.yml`)

### Documentation & UX

- [ ] Architecture guide (`plan_docs/agent-loop-refactor/architecture.md`)
- [ ] Updated `plan_docs/github-app-webhook-setup.md` with beads trigger documentation
- [ ] README update with beads workflow examples

### Build & Distribution

- [ ] Multi-stage Dockerfile.webhook with Rust builder
- [ ] `install-dev-tools.ps1` installs `br` and `bvr` locally

### Infrastructure & DevOps

- [ ] CI workflow unchanged (validate.yml: lint, scan, test, build)
- [ ] Docker build verified in CI `build` job

---

## Acceptance Criteria

- [ ] `br` and `bvr` binaries available in webhook-receiver Docker image
- [ ] `/perfect-idea` skill produces a filled-in `application_plan.md` from interactive conversation
- [ ] `/plan-to-beads` skill produces a valid `.beads/` DAG from an application plan
- [ ] `BeadsLoop` background thread starts on webhook-receiver startup and polls `br ready --json`
- [ ] Per-bead workspace isolation: each bead gets its own clone at `/workspace/<bead_id>/`
- [ ] Agent spawned via `prompt.ps1 -Workspace /workspace/<bead_id>/` completes task and runs `br close`
- [ ] On success: branch pushed, PR created, bead closed, workspace cleaned
- [ ] On failure: retry up to 3x with error context injected; halt on max retries
- [ ] Existing label-driven orchestration continues to work unchanged
- [ ] `validate.ps1 -All` passes clean

## Risk Mitigation Strategies

| Risk | Mitigation |
|------|------------|
| `br`/`bvr` CLI args change between versions | Pin cargo install to specific commit SHA; validate CLI args in integration tests |
| Agent fails to run `br close` (prompt non-compliance) | `beads_loop.py` verifies bead status via `br show <id> --json` after agent exits; retries if still open |
| Concurrent webhook deliveries spawn duplicate loops | Per-workspace lock in `beads_loop.py` prevents duplicate loops for same repo |
| Docker build time increases significantly with Rust stage | Use Docker layer caching; Rust builder stage only rebuilds on Cargo changes |
| Per-bead clone consumes excessive disk space | Cleanup workspace after bead completion (success or final failure); configurable workspace root |
| `br ready --json` returns empty when graph is blocked | Loop exits cleanly; new beads from `/plan-to-beads` re-trigger polling |

## Success Metrics

- End-to-end: user comments `/perfect-idea` on issue → application plan generated → `/plan-to-beads` → beads created → agents autonomously implement tasks → PRs created
- Zero manual intervention required for beads that pass on first attempt
- Failed beads retry with error context and resolve without human intervention >50% of the time

## Repository Branch

Target branch for implementation: `main`

## Implementation Notes

- **Corrections from original plans**: See `architecture.md` for detailed corrections (`bvr --robot-ready` doesn't exist, port 4099 not 4096, async not sync dispatch, etc.)
- **Coexistence**: The existing label-driven orchestration prompt is unchanged. Beads pipeline is additive.
- **Maestro/supervisor**: Explicitly out of scope. See `plan_docs/maestro_architecture_options.md` for future consideration.
- **Beads CLI reference**: `br ready --json` (list ready tasks), `bvr --robot-next` (single best task), `br close <id> --reason "..."` (close task), `br sync --flush-only` (export JSONL before git commit).
