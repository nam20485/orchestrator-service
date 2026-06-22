# Gemini Context Guide — Orchestrator Service

Welcome! This is the definitive context, architecture reference, and instruction guide for AI agents working in this repository.

---

## 1. Project Overview & Architecture

`orchestrator-service` is a **three-tier software factory** built around a Dockerized OpenCode server, client automation scripts, and a FastAPI-based GitHub App webhook receiver that dispatches agent workflows using a background task manager and a DAG-based runner.

### The Three-Tier Beads Pipeline

| Phase | Actor / Trigger | Input | Action | Output |
| :--- | :--- | :--- | :--- | :--- |
| **1. Ideation** | `/perfect-idea` (PM Agent skill) | Loose human idea | Interrogates constraints & architecture via conversation | `application_plan.md` |
| **2. Planning** | `/plan-to-beads` (Scrum Agent skill) | `application_plan.md` | Derives Epics/Tasks/ACs and maps DAG dependencies | `.beads/` Graph DAG |
| **3. Execution** | `BeadsLoop` (background daemon thread) | `.beads/` Graph DAG | Spawns isolated agents per bead to write code, test, and close beads | Working Software + PRs |

*Note: The Beads pipeline is fully additive and coexists with the legacy label-driven orchestration (`orchestration_prompt.jinja2.md`).*

### Service Roles

1.  **`orchestratorservice`** (OpenCode Server)
    *   Exposed on port **`4099`** (e.g. `http://localhost:4099`).
    *   Runs the `opencode serve` command inside a Docker container.
    *   Hosts agent sessions. Config files (`opencode.json`, `AGENTS.md`, and `.opencode/` agents/commands directories) are copied to `/app` inside the image and loaded using environment variables (`OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR`).
    *   Runs agent workspaces in `/workspace` (backed by the `opencode-workspace` shared volume), keeping them separate from server config files.
2.  **`webhook-receiver`** (FastAPI App)
    *   Binds internally to port **`8080`**.
    *   Validates signed GitHub App webhooks and dispatches runs asynchronously via `scripts/prompt.ps1`.
    *   Spawns the `BeadsLoop` background daemon thread which polls `br ready --json` (every 10s by default) to identify and execute ready tasks.
3.  **`webhook-proxy`** (Caddy)
    *   Binds to host port **`80`** by default (HTTP).
    *   Terminates TLS/HTTPS on host port **`443`** if running production deployment with `compose.https.yaml` overlay.

---

## 2. Normal Startup States vs. Real Failures

When interacting with the logs or supervising services, understand this distinction:

*   **"Beads not initialized"** (logs indicating `NOT_INITIALIZED` or waiting for `/plan-to-beads`) is a **NORMAL, IDLE startup state**, not an error. The service is designed to start "ready at will" before any task DAG is created. When a planning action is triggered, `BeadsLoop` will automatically pick up work without requiring a service restart.
*   **Empty `br ready --json`** indicates there are no unblocked tasks ready. This is a **NORMAL idle state**.
*   **Database locks or unexpected file failures** on `br ready` are actual errors logged at `ERROR` level.
*   **Agent failures to run `br close`** are caught by retry logic (up to `BEADS_MAX_RETRIES`, default: 3) and logged; they do not trigger a service crash.

---

## 3. Environment Variables & Authentication

The project depends on host/CI environment variables injected at startup by `scripts/docker-entrypoint.sh` (which writes `/root/.local/share/opencode/auth.json` before `opencode serve` starts). **Do not use `.env` files for Compose.**

### Core Configurations

| Environment Variable | Default | Purpose |
| :--- | :--- | :--- |
| `OS_WEBHOOK_SECRET` | *(Required)* | Webhook secret for validating `X-Hub-Signature-256` signatures |
| `OPENCODE_SERVER_PASSWORD` | *(Required)* | Password used to authenticate with `opencode serve` |
| `OPENCODE_SERVER_URL` | `http://localhost:4099` | URL to target the OpenCode server |
| `ORCHESTRATOR_WORKSPACE` | `/workspace` | Default path passed to `opencode run --dir` |
| `BEADS_ENABLED` | `true` | Toggles the `BeadsLoop` background execution thread |
| `BEADS_POLL_INTERVAL` | `10` | Frequency in seconds of polling the Beads DAG |
| `BEADS_MAX_RETRIES` | `3` | Maximum attempts per bead before requesting human intervention |
| `BEADS_WORKSPACE_ROOT` | `/workspace` | Working directory for task branches and clones |

### Model & Provider Credentials

Supported API keys dynamically converted at container start:
*   `ZAI_CODING_API_KEY` (or `ZAI_API_KEY`): Used for `zai-coding-plan/` models (e.g. `glm-4.7`).
*   `OPENROUTER_API_KEY`: Model routing via OpenRouter.
*   `MODEL_STUDIO_API_KEY`: Alibaba Model Studio Singapore (`bailian-payg`). Defaults to `bailian-payg/qwen3.6-plus` (large model) and `bailian-payg/qwen3.6-flash` (small model).

---

## 4. Operational & Running Commands

### Dependency Management (Python)

This project uses **uv** to manage dependencies. Never use global `pip install`.
*   Sync dependencies (including dev): `uv sync --group dev`
*   Run FastAPI app locally: `uv run orchestrator-webhook` or `uv run python -m webhook_receiver`

### Multi-Container Stack (Docker)

*   **Development / Local Setup** (HTTP port 80 / Caddy on host; compatible with Tailscale Funnel):
    ```bash
    docker compose up --build
    ```
    *Note: Do not merge compose.https.yaml while Tailscale Funnel is active, since both bind to host port 443.*
*   **Production Deployment** (HTTPS port 443 with Caddy TLS):
    ```bash
    COMPOSE_FILE=compose.yaml:compose.https.yaml docker compose up --build
    ```

### Client Operations (pwsh)

Local execution of tasks or interactive server connections requires `pwsh` (PowerShell) and a local `opencode` installation:
*   **One-shot client prompt**:
    ```bash
    pwsh -NoProfile -File scripts/prompt.ps1 -Prompt "Your instructions" -ServerUrl http://localhost:4099
    ```
*   **Attach to current server session**:
    ```bash
    opencode attach http://localhost:4099
    ```

### Webhook Simulator (Local Testing)

Set `WEBHOOK_ENABLE_SIMULATOR=1` before starting the FastAPI app. Open `http://localhost/simulator` (or `http://localhost:8080/simulator`) to access:
*   **Safe (ping) tab**: signed test ping (returns 200, no execution).
*   **Work events tab**: mock events (e.g., `issues`) that dispatch real runs (returns 202).

---

## 5. The Validation & Quality Contract

Before committing any code or configuration change, local validation is **mandatory**. This mirrors the GitHub Actions `.github/workflows/validate.yml` pipeline.

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

### Specific Validation Switches

| Command | Action |
| :--- | :--- |
| `validate.ps1 -Lint` | Runs Python `ruff` linting, GitHub `actionlint`, Docker compose configs, Caddyfile syntactic check, and shellcheck checks on shell scripts. |
| `validate.ps1 -Scan` | Scans changed, unstaged, and staged files for accidental credential or API key leaks. |
| `validate.ps1 -Test` | Executes `pytest` for FastAPI & loop modules, `Pester` tests for PowerShell modules, and entrypoint bash script validation. |

*Note: Missing dev tools can be auto-installed locally via: `pwsh -NoProfile -File ./scripts/install-dev-tools.ps1`*

---

## 6. Pre-Commit & Diagnostics Checklist

### Secret Prevention

*   **DO NOT** write or commit real API keys, credentials, or personal tokens (`ghp_...`, `sk-...`, etc.) inside any code or test fixture.
*   **ONLY** use `FAKE-KEY-FOR-TESTING-...` placeholders in test fixtures.
*   Before committing, ensure your secret scan passes cleanly. Leverage the repo pre-commit scripts `.cursor/skills/scan-uncommitted-secrets/scripts/scan.sh`.

### Diagnostics from GHA Failures

If a GitHub Actions workflow fails on push or PR:
1.  **Do not guess** the root cause or theorize about fixes.
2.  Use the GitHub CLI to view exact run logs:
    ```bash
    gh run list --workflow=validate.yml --limit 5
    gh run view <run-id> --log-failed
    ```
3.  Locate, analyze, and cite the **exact failure lines** from the logs when resolving the issue.

### Outdated Architectural Context

Ignore the following legacy documents; they do not match the current production system:
*   `plan_docs/archive/plan.md`
*   `plan_docs/archive/orchestration_supervisor.md`
*   `plan_docs/archive/maestro_architecture_options.md`
*   `docs/agent-loop-dev-plans/` (with exception of `plan_docs/agent-loop-refactor/architecture.md`)
