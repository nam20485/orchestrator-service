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
export OS_WEBHOOK_SECRET='…'   # from GitHub App → Webhook secret
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

Logs for the last dispatched run: `/tmp/orchestrator-webhook/last-prompt.md` and `last-run.stderr`.

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
