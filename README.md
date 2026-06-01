# orchestrator-service

Dockerized OpenCode server (`opencode serve` on port **4099**) plus client scripts and a GitHub App webhook receiver that forwards events to the orchestrator via `scripts/prompt.ps1` (requires **pwsh**).

## OpenCode server + webhook receiver (Docker)

```bash
export OPENCODE_SERVER_PASSWORD='…'
export ZAI_CODING_API_KEY='…'   # and/or OPENROUTER_API_KEY
export GITHUB_WEBHOOK_SECRET='…'   # GitHub App webhook secret
docker compose up --build
```

- **orchestratorservice** — OpenCode server on port **4099**
- **webhook-receiver** — internal FastAPI app (`POST /webhooks/github`)
- **webhook-proxy** — Caddy reverse proxy on ports **80** / **443** in front of the receiver

Point your GitHub App webhook at `https://<host>/webhooks/github`. For automatic TLS, set `WEBHOOK_SITE_ADDRESS=hooks.example.com` (DNS must point here) before `docker compose up`. Default `:80` is HTTP only (local or TLS terminated elsewhere). Full setup: [docs/github-app-webhook-setup.md](docs/github-app-webhook-setup.md).

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
export GITHUB_WEBHOOK_SECRET='…'   # from GitHub App → Webhook secret
export OPENCODE_SERVER_URL=http://localhost:4099
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
| `GITHUB_WEBHOOK_SECRET` | *(required)* | Webhook secret for `X-Hub-Signature-256` |
| `OPENCODE_SERVER_URL` | `http://localhost:4099` | OpenCode server for `prompt.ps1` |
| `ORCHESTRATOR_WORKSPACE` | `/workspace` | `--dir` passed to `opencode run` |
| `PROMPT_SCRIPT` | `scripts/prompt.ps1` | PowerShell prompt launcher (requires `pwsh`) |
| `OPENCODE_MODEL` | `zai-coding-plan/glm-4.7-flash` | Model |
| `OPENCODE_AGENT` | `orchestrator` | Agent |
| `WEBHOOK_ALLOWED_EVENTS` | *(all)* | Optional comma-separated event filter |
| `WEBHOOK_MAX_PAYLOAD_CHARS` | `120000` | Max JSON chars embedded in prompt |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | `0.0.0.0` / `8080` | HTTP bind |

Logs for the last dispatched run: `/tmp/orchestrator-webhook/last-prompt.md` and `last-run.stderr`.

### Health

```bash
curl -s http://localhost:8080/health
```
