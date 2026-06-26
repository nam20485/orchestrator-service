# orchestrator-service

Dockerized OpenCode server (`opencode serve` on port **4099**) plus client scripts and a GitHub App webhook receiver that forwards events to the orchestrator via `scripts/prompt.ps1` (requires **pwsh**).

## Three-Tier Pipeline

The system is a three-tier software factory built around the [Beads](https://github.com/Dicklesworthstone/beads_rust) DAG ecosystem. Authoritative architecture: [`plan_docs/agent-loop-refactor/architecture.md`](plan_docs/agent-loop-refactor/architecture.md).

| Phase | Trigger | Actor | Output |
|-------|---------|-------|--------|
| **1. Ideation** | `/perfect-idea` skill | PM agent | `application_plan.md` |
| **2. Planning** | `/plan-to-beads` skill | Scrum agent | `.beads/` DAG |
| **3. Execution** | (automatic) | `BeadsLoop` background thread | Working software + PRs |

The `BeadsLoop` runs as a background daemon thread in `webhook-receiver`, polling `br ready --json` every 10 s and spawning isolated agents per bead to implement, test, and close each task.

> **"Ready at will."** The service starts before any work is planned. On a fresh `/workspace` (no `.beads/` yet), `BeadsLoop` logs `INFO` once ("Beads not initialized — waiting for /plan-to-beads") and stays idle. When a user triggers `/plan-to-beads`, beads are created and the loop picks them up automatically — no restart required. This is a **normal state**, not an error.

## OpenCode server + webhook receiver (Docker)

```bash
export OPENCODE_SERVER_PASSWORD='…'
export ZAI_CODING_API_KEY='…'   # and/or OPENROUTER_API_KEY, MODEL_STUDIO_API_KEY
export OS_WEBHOOK_SECRET='…'   # GitHub App webhook secret
export WORKSPACE_DIR='…'       # host directory mounted at /workspace (agent working trees)
docker compose up --build
```

`WORKSPACE_DIR` is **required** — it is the host directory bind-mounted into both `orchestratorservice` and `webhook-receiver` at `/workspace`. This is where agent sessions run and where `.beads/` DAG state lives. Create it before first start (e.g. `mkdir -p ~/orchestrator-workspace && export WORKSPACE_DIR=~/orchestrator-workspace`).

> **Migration from the old named volume:** If you previously ran with the `opencode-workspace` named volume, copy its contents to your host directory before starting:
> ```bash
> docker run --rm -v opencode-workspace:/src -v "$WORKSPACE_DIR":/dst alpine sh -c 'cp -a /src/. /dst/'
> ```
> The old named volume can then be removed (`docker volume rm opencode-workspace`).

- **orchestratorservice** — OpenCode server on port **4099**
- **webhook-receiver** — internal FastAPI app (`POST /webhooks/github`)
- **webhook-proxy** — Caddy on host **:80** (default compose); add **`compose.https.yaml`** for host **:443** when Caddy terminates TLS

| Environment | Compose command | HTTPS edge |
|-------------|-----------------|------------|
| Local + [Tailscale Funnel](docs/github-app-webhook-setup.md#local-development-with-tailscale-funnel--docker-compose) | `docker compose up` | Funnel → `:80` (do **not** use `compose.https.yaml` while Funnel is on) |
| Production (own domain) | `COMPOSE_FILE=compose.yaml:compose.https.yaml docker compose up` | Caddy + Let's Encrypt on `:443` |

Point your GitHub App webhook at `https://<host>/webhooks/github`. For Caddy TLS, set `WEBHOOK_SITE_ADDRESS=hooks.example.com` (DNS must point here) and include `compose.https.yaml`. Default `:80` is HTTP only (Funnel/ngrok terminate HTTPS upstream).

**Compose overlays:** base `compose.yaml` + optional `compose.https.yaml` merge (see [Docker Compose overlays](docs/github-app-webhook-setup.md#docker-compose-overlays-dev-vs-prod-https) in the webhook setup doc). Full setup: [docs/github-app-webhook-setup.md](docs/github-app-webhook-setup.md).

## Client prompt (one-shot)

Requires local `opencode` and `pwsh`:

```bash
pwsh -NoProfile -File scripts/prompt.ps1 -Prompt "Summarize open issues" -ServerUrl http://localhost:4099
```

Large prompts: `-PromptFile /path/to/prompt.md`.

## GitHub webhook receiver

Python app (managed with **uv**) that validates GitHub App webhooks and dispatches orchestration runs in the background.

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

Content type: `application/json`. Subscribe to the events you need; restrict with `WEBHOOK_ALLOWED_EVENTS` (comma-separated, e.g. `issues,pull_request,workflow_run`).

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OS_WEBHOOK_SECRET` | *(required)* | Webhook secret for `X-Hub-Signature-256` |
| `OPENCODE_SERVER_URL` | `http://localhost:4099` | OpenCode server for `prompt.ps1` |
| `ORCHESTRATOR_WORKSPACE` | `/workspace` | `--dir` passed to `opencode run` |
| `PROMPT_SCRIPT` | `scripts/prompt.ps1` | PowerShell prompt launcher (requires `pwsh`) |
| `OPENCODE_MODEL` | `bailian-payg/qwen3.6-plus` | Model |
| `OPENCODE_AGENT` | `orchestrator` | Agent |
| `WEBHOOK_ALLOWED_EVENTS` | *(all)* | Optional comma-separated event filter |
| `WEBHOOK_MAX_PAYLOAD_CHARS` | `120000` | Max JSON chars embedded in prompt |
| `WEBHOOK_MAX_BODY_BYTES` | `26214400` (25 MiB) | Reject webhook POST bodies larger than this |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | `0.0.0.0` / `8080` | HTTP bind |
| `WEBHOOK_ENABLE_SIMULATOR` | `0` | Serve dev UI at `/simulator` when set to `1` |
| `BEADS_ENABLED` | `true` | Enable the `BeadsLoop` background execution thread |
| `BEADS_POLL_INTERVAL` | `10` | Seconds between `br ready --json` polls |
| `BEADS_MAX_RETRIES` | `3` | Max retries per bead before halting for human intervention |
| `BEADS_WORKSPACE_ROOT` | `/workspace` | Root directory for beads DAG and per-bead clones |
| `BEADS_TARGET_REPO` | *(empty)* | Target repo URL for per-bead workspace clones |

Per-dispatch run logs: `/tmp/orchestrator-webhook/prompt-*.md` (the prompt) and `prompt-*.stderr` (pwsh stderr).

### Webhook simulator (local dev)

When `WEBHOOK_ENABLE_SIMULATOR=1`, open:

```text
http://localhost/simulator
```

- **Safe (ping)** tab — signed `ping` delivery; returns **200**, no orchestration.
- **Work events** tab — `issues`, `pull_request`, etc.; returns **202** and starts a real OpenCode run.

For local simulator UI, set `WEBHOOK_ENABLE_SIMULATOR=1` before `docker compose up`. Secret is pre-filled from `OS_WEBHOOK_SECRET`; browser `sessionStorage` overrides if you edit the field.

### Orchestration dashboard

A real-time web UI for the Beads pipeline: bead DAG status, active agents, and a live event timeline (SSE). Served by `webhook-receiver` behind Caddy.

```text
http://localhost/dashboard
```

UI status badges, sortable bead table with inline logs, event timeline, and a JSON API under `/api/dashboard/*`. Full reference: [docs/dashboard.md](docs/dashboard.md).

> **Trusted-network only** — the dashboard has no authentication; run it only inside a trusted network like the rest of the receiver.

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
