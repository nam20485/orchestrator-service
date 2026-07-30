# orchestrator-service

Dockerized OpenCode server (`opencode serve` on port **4099**) plus client scripts and a GitHub App webhook receiver that forwards events to the orchestrator via `scripts/prompt.ps1` (requires **pwsh**).

## Three-Tier Pipeline

The system is a three-tier software factory built around the [Beads](https://github.com/Dicklesworthstone/beads_rust) DAG ecosystem. Authoritative architecture: [`docs/deployment-compose.md`](docs/deployment-compose.md) (runtime) and [`plan_docs/three-repo-oveall-architecture-inspection-update-plan.md`](plan_docs/three-repo-oveall-architecture-inspection-update-plan.md) (multi-repo factory).

| Phase | Trigger | Actor | Output |
|-------|---------|-------|--------|
| **1. Ideation** | `/perfect-idea` skill | PM agent | `application_plan.md` |
| **2. Planning** | `/plan-to-beads` skill | Scrum agent | `.beads/` DAG |
| **3. Execution** | (automatic) | `BeadsLoop` background thread | Working software + PRs |

The `BeadsLoop` runs as a background daemon thread in `webhook-receiver`. It scans `/workspace/<project-slug>/` for projects (subdirs containing `.beads/`), then for each project selects the next bead via `bvr --robot-next` (graph-aware) — falling back to `br ready --json` + priority sort when `bvr` is unavailable — and spawns isolated agents in per-bead git worktrees (`.worktrees/<bead-id>/`) to implement, test, and close each task.

> **"Ready at will."** The service starts before any work is planned. When no projects exist (no `.beads/` dirs found), `BeadsLoop` stays idle. When a user triggers `/plan-to-beads` in a project workspace, beads are created and the loop discovers the project on its next scan — no restart required. This is a **normal state**, not an error.

**Normal states vs. real errors:** "beads not initialized" (`NOT_INITIALIZED`) and an empty `bvr`/`br ready` result (no unblocked beads) are both **normal idle states**, not errors. Real errors are non-`NOT_INITIALIZED` failures from `bvr`/`br ready` (e.g. `db locked`), logged at `ERROR` level. An agent's failure to run `br close` is caught by retry logic (up to `BEADS_MAX_RETRIES`, default 3) — it does not crash the service.

## OpenCode server + webhook receiver (Docker)

```bash
export OPENCODE_SERVER_PASSWORD='…'
export ZAI_CODING_API_KEY='…'   # and/or OPENROUTER_API_KEY, MODEL_STUDIO_API_KEY
export OS_WEBHOOK_SECRET='…'   # GitHub App webhook secret
export WORKSPACE_DIR='…'       # host directory mounted at /workspace (agent working trees)
docker compose up --build
```

Provider credentials are injected at container start by `scripts/docker-entrypoint.sh` (which writes `/home/app/.local/share/opencode/auth.json` before `opencode serve`): `ZAI_CODING_API_KEY` (alias `ZAI_API_KEY`, for `zai-coding-plan/` models such as `glm-5`), `OPENROUTER_API_KEY`, and `MODEL_STUDIO_API_KEY` (Alibaba Model Studio Singapore, provider `bailian-payg`; sets provider auth only — the default model comes from `image/.opencode/opencode.json`, currently `zai-coding-plan/glm-5` large / `zai-coding-plan/glm-4.5-air` small). Compose reads these from the host/CI environment — **no project `.env` file**.

`WORKSPACE_DIR` is **required** — it is the host directory bind-mounted into both `orchestratorservice` and `webhook-receiver` at `/workspace`. This is where agent sessions run. Projects live in subdirectories (`/workspace/<project-slug>/`), each containing its own `.beads/` DAG and per-bead git worktrees. Create it before first start (e.g. `mkdir -p ~/orchestrator-workspace && export WORKSPACE_DIR=~/orchestrator-workspace`).

> **Migration from the old named volume:** If you previously ran with the `opencode-workspace` named volume, copy its contents to your host directory before starting:
> ```bash
> docker run --rm -v opencode-workspace:/src -v "$WORKSPACE_DIR":/dst alpine sh -c 'cp -a /src/. /dst/'
> ```
> The old named volume can then be removed (`docker volume rm opencode-workspace`).

### Non-root execution

All three containers run as a non-root user (`app`, UID 1000 by default; Caddy runs as a `caddy` user created in the image — the pinned upstream `caddy:2.10.0-alpine` ships none). This means files created in `WORKSPACE_DIR` are owned by the host operator — no `sudo` needed to delete or modify them.

The `app`/`caddy` users and their file ownership are baked into the images at **build** time (`ARG APP_UID`/`APP_GID`, default 1000, configure the `app` user only), and all three start as root so their entrypoint can `chown` named volumes and then drop privileges (`gosu` for `app`, `su-exec` for `caddy`). For this reason the compose files intentionally set **no** runtime `user:` — a runtime UID override would bypass the drop and start the container as an arbitrary numeric UID that cannot write the 1000-owned `/home/app` or `/app/.memory`.

**Override the UID/GID** if your host user is not UID 1000 by **rebuilding** the images so the `app` user is re-baked to match your host:

```bash
docker compose -f compose.yaml -f compose.build.yaml build \
  --build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)
docker compose -f compose.yaml -f compose.build.yaml up -d
```

**One-time migration** for pre-existing root-owned workspace files (from before this change):

```bash
sudo chown -R $(id -u):$(id -g) "$WORKSPACE_DIR"
```

**Caddy** binds privileged ports (`:80`/`:443`) as non-root via `CAP_NET_BIND_SERVICE` (added in compose). No additional configuration needed.

- **orchestratorservice** — OpenCode server on port **4099**
- **webhook-receiver** — internal FastAPI app (`POST /webhooks/github`)
- **webhook-proxy** — Caddy on host **:80** (default compose); add **`compose.https.yaml`** for host **:443** when Caddy terminates TLS

| Environment | Compose command | HTTPS edge |
|-------------|-----------------|------------|
| Local + Tailscale Funnel | `docker compose up` | Funnel → `:80` (do **not** use `compose.https.yaml` while Funnel is on — Funnel and Caddy both bind host `:443`) |
| Production (own domain) | `COMPOSE_FILE=compose.yaml:compose.https.yaml docker compose up` | Caddy + Let's Encrypt on `:443` |

Point your GitHub App webhook at `https://<host>/webhooks/github`. For Caddy TLS, set `WEBHOOK_SITE_ADDRESS=hooks.example.com` (DNS must point here) and include `compose.https.yaml`. Default `:80` is HTTP only (Funnel/ngrok terminate HTTPS upstream).

**Local development with Tailscale Funnel:** run `tailscale funnel 80` to publish a stable `*.ts.net` HTTPS URL onto host `:80` (Caddy). Use `compose.yaml` only — do not add `compose.https.yaml` (both bind `:443`).

**Compose overlays:** base `compose.yaml` defines the three services over HTTP on `:80`. The optional `compose.https.yaml` overlay merges on top and makes Caddy terminate TLS on `:443` (Let's Encrypt) when you own a domain pointing at the host. Local dev uses the base file alone; production uses `COMPOSE_FILE=compose.yaml:compose.https.yaml`.

## Client prompt (one-shot)

Requires local `opencode` and `pwsh`:

```bash
pwsh -NoProfile -File scripts/prompt.ps1 -Prompt "Summarize open issues" -ServerUrl http://localhost:4099
```

Large prompts: `-PromptFile /path/to/prompt.md`.

## GitHub webhook receiver

Python app (managed with **uv** — always use `uv`, never global `pip install`) that validates GitHub App webhooks and dispatches orchestration runs in the background.

### Setup

```bash
uv sync
export OS_WEBHOOK_SECRET='…'   # from GitHub App → Webhook secret
export OPENCODE_SERVER_URL=http://localhost:4099
export OPENCODE_SERVER_PASSWORD='…'   # required when the target OpenCode server is password-protected
export ORCHESTRATOR_WORKSPACE=/workspace   # or a host path with a clone
# optional: export GH_ORCHESTRATION_AGENT_TOKEN / GITHUB_TOKEN for gh in the agent
```

### Run

```bash
uv run orchestrator-webhook
# or
uv run python -m webhook_receiver
```

Listens on `WEBHOOK_HOST`:`WEBHOOK_PORT` (default `0.0.0.0:8080`).

### GitHub App webhook URL

Point the app webhook to:

```text
https://<your-host>/webhooks/github
```

Content type: `application/json`. Subscribe to **Issues** on the GitHub App. The receiver only dispatches the orchestrator for `issues.labeled` events from a non-bot actor carrying a workflow label; all other deliveries are acknowledged but ignored. The dispatch set (defined in `webhook_receiver/filters.py`) is:

- labels in the `orchestration:*` or `gh-issue-tracking:*` prefix namespaces, or the exact labels `implementation:ready` / `implementation:complete`;
- the special `gh-issue-tracking:direct-body` label runs the **issue body verbatim** as the orchestrator prompt. Because that prompt inherits the orchestration GitHub token and `--auto`, it is fail-closed-gated by `DIRECT_BODY_ALLOWED_SENDERS` (comma-separated trusted-sender allowlist; when unset/empty, or the sender is not listed, the delivery is ignored).

### Environment variables

The authoritative, complete list (with defaults and descriptions) lives in [`docs/environment-variables.md`](docs/environment-variables.md). The most common ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `OS_WEBHOOK_SECRET` | *(required)* | Webhook secret for `X-Hub-Signature-256` |
| `OPENCODE_SERVER_URL` | `http://localhost:4099` | OpenCode server for `prompt.ps1` |
| `ORCHESTRATOR_WORKSPACE` | `/workspace` | `--dir` passed to `opencode run` |
| `OPENCODE_MODEL` | `zai-coding-plan/glm-5` | Model used for dispatched runs |
| `OPENCODE_VARIANT` | `high` | Reasoning-effort variant passed to opencode via `--variant` |
| `OPENCODE_AGENT` | `orchestrator` | Agent |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | `0.0.0.0` / `8080` | HTTP bind |
| `WEBHOOK_ENABLE_SIMULATOR` | `0` | Serve dev UI at `/simulator` when set to `1` (also requires `DASHBOARD_TOKEN`) |
| `DASHBOARD_TOKEN` | *(unset → disabled)* | Shared secret gating the dashboard **and** simulator |
| `BEADS_ENABLED` | `true` | Enable the `BeadsLoop` background execution thread |
| `BEADS_WORKSPACE_ROOT` | `/workspace` | Base directory containing per-project workspaces (`/workspace/<slug>/`) |

The env doc additionally covers `DIRECT_BODY_ALLOWED_SENDERS`, the full watchdog set (`IDLE_TIMEOUT_SECS`, `HARD_CEILING_SECS`, `MAX_CONSECUTIVE_ERRORS`, `PERMISSION_ASK_GRACE_SECS`, …), `OPENCODE_SERVER_LOG_PATH`, and `TRACE_BLACKLIST_PATTERNS`.

Per-dispatch run logs: `/tmp/orchestrator-webhook/prompt-*.md` (the prompt) and `prompt-*.stderr` (pwsh stderr).

### Webhook simulator (local dev)

When `WEBHOOK_ENABLE_SIMULATOR=1`, open:

```text
http://localhost/simulator
```

- **Safe (ping)** tab — signed `ping` delivery; returns **200**, no orchestration.
- **Work events** tab — `issues`, `pull_request`, etc.; returns **202** and starts a real OpenCode run.

For local simulator UI, set both `WEBHOOK_ENABLE_SIMULATOR=1` **and** `DASHBOARD_TOKEN` before `docker compose up`. The simulator is token-gated the same way as the dashboard: with `WEBHOOK_ENABLE_SIMULATOR=1` but `DASHBOARD_TOKEN` unset, `/simulator` returns **401** ("Simulator requires DASHBOARD_TOKEN to be set") — present the token via `Authorization: Bearer`, `?token=`, or the `dashboard_token` cookie (`webhook_receiver/simulator.py`). Secret is pre-filled from `OS_WEBHOOK_SECRET`; browser `sessionStorage` overrides if you edit the field.

### Orchestration dashboard

A real-time web UI for the Beads pipeline: bead DAG status, active agents, and a live event timeline (SSE). Served by `webhook-receiver` behind Caddy.

```text
http://localhost/dashboard
```

UI status badges, sortable bead table with inline logs, event timeline, and a JSON API under `/api/dashboard/*`. Full reference: [docs/dashboard.md](docs/dashboard.md).

> **Token-gated.** The dashboard is **disabled by default** (every route returns `404`) until `DASHBOARD_TOKEN` is set. Once set, requests must present that token via an `Authorization: Bearer` header, a `?token=` query parameter, or a `dashboard_token` cookie (constant-time compared); a missing/wrong token returns `401`. Set `DASHBOARD_TOKEN` before enabling it (`webhook_receiver/auth.py`).

### Health

```bash
curl -s http://localhost:8080/health
```

## Validation

Prerequisites: **pwsh**, **uv**, **docker** (compose/caddy tests), **jq**, optional **actionlint** / **shellcheck**.

```bash
pwsh -NoProfile -File ./scripts/install-dev-tools.ps1   # first time
pwsh -NoProfile -File ./scripts/validate.ps1 -All         # before commit
```

CI runs [`.github/workflows/validate.yml`](.github/workflows/validate.yml) on PRs: **lint**, **scan**, **test**, **build** (Docker images; build is CI-only, not in local `-All`). See [AGENTS.md](AGENTS.md) for the full validation contract.

## Secrets & fixtures

Never write or commit real credentials or tokens (`ghp_…`, `sk-…`, `AKIA…`) in code or fixtures. In test fixtures use only `FAKE-KEY-FOR-TESTING-…` placeholders. The secret scan (`validate.ps1 -Scan` / `.cursor/skills/scan-uncommitted-secrets`) must pass cleanly before commit.

## Diagnosing CI failures

Never guess at GitHub Actions failures — read the actual logs and cite the exact failing lines:

```bash
gh run list --workflow=validate.yml --limit 5
gh run view <run-id> --log-failed
```

## Legacy / outdated docs

These historical documents do **not** reflect the current architecture — do not use them for implementation guidance:

- [`plan_docs/.archived/plan.md`](plan_docs/.archived/plan.md) (original OpenCode Server POR, port 4096→4099), [`plan_docs/.deferred/maestro_supervisor/orchestration_supervisor.md`](plan_docs/.deferred/maestro_supervisor/orchestration_supervisor.md) (future maestro/supervisor, not implemented), [`plan_docs/.deferred/maestro_supervisor/maestro_architecture_options.md`](plan_docs/.deferred/maestro_supervisor/maestro_architecture_options.md) (future architecture options, not implemented), and the archived webhook-setup guide under [`plan_docs/.archived/agent-loop-refactor/`](plan_docs/.archived/agent-loop-refactor/)
- Current architecture (replaces the former `docs/agent-loop-dev-plans/` + `plan_docs/agent-loop-refactor/` pointers, which no longer exist): [`docs/deployment-compose.md`](docs/deployment-compose.md) and [`plan_docs/three-repo-oveall-architecture-inspection-update-plan.md`](plan_docs/three-repo-oveall-architecture-inspection-update-plan.md)
