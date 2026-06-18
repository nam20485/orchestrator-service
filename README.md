# orchestrator-service

Dockerized OpenCode server (`opencode serve` on port **4099**) plus client scripts and a GitHub App webhook receiver that forwards events to the orchestrator via `scripts/prompt.ps1` (requires **pwsh**).

## OpenCode server + webhook receiver (Docker)

```bash
export OPENCODE_SERVER_PASSWORD='…'
export ZAI_CODING_API_KEY='…'   # and/or OPENROUTER_API_KEY, MODEL_STUDIO_API_KEY
export OS_WEBHOOK_SECRET='…'   # GitHub App webhook secret
docker compose up --build
```

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

Per-dispatch run logs: `/tmp/orchestrator-webhook/prompt-*.md` (the prompt) and `prompt-*.stderr` (pwsh stderr).

### Webhook simulator (local dev)

When `WEBHOOK_ENABLE_SIMULATOR=1`, open:

```text
http://localhost/simulator
```

- **Safe (ping)** tab — signed `ping` delivery; returns **200**, no orchestration.
- **Work events** tab — `issues`, `pull_request`, etc.; returns **202** and starts a real OpenCode run.

For local simulator UI, set `WEBHOOK_ENABLE_SIMULATOR=1` before `docker compose up`. Secret is pre-filled from `OS_WEBHOOK_SECRET`; browser `sessionStorage` overrides if you edit the field.

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
