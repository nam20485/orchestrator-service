# Getting started

Use the Compose development stack to run the shipped images, or add `compose.build.yaml` when testing changes to source baked into the containers. Local Python work uses `uv`; the project does not use a global `pip` environment.

The runtime requires a writable workspace directory and an OpenCode server password. Webhook delivery additionally requires a GitHub webhook secret. Keep provider and GitHub credentials in your shell or secret manager, never in committed files.

## Prerequisites

- Docker with Compose.
- A host directory to mount as `/workspace`.
- `pwsh` for `scripts/*.ps1` wrappers and validation.
- `uv` for Python dependencies and tests.
- An OpenCode provider credential accepted by `scripts/docker-entrypoint.sh`.
- Values for `OPENCODE_SERVER_PASSWORD`, `OS_WEBHOOK_SECRET`, and `WORKSPACE_DIR`.

`docs/environment-variables.md` lists the complete runtime contract. The Compose definitions fail early when `WORKSPACE_DIR` or `OPENCODE_SERVER_PASSWORD` is absent.

## Start the development stack

```bash
mkdir -p "$HOME/orchestrator-workspace"
export WORKSPACE_DIR="$HOME/orchestrator-workspace"
export OPENCODE_SERVER_PASSWORD='…'
export OS_WEBHOOK_SECRET='…'
export ZAI_CODING_API_KEY='…'

docker compose -f compose.development.yaml up -d
```

To build the images from the current checkout instead of pulling development tags:

```bash
docker compose \
  -f compose.development.yaml \
  -f compose.build.yaml \
  up -d --build
```

The layering and environment differences are documented in `docs/deployment-compose.md`. The base production file is `compose.yaml`; `compose.https.yaml` adds Caddy TLS for an owned hostname.

## Check the runtime

```bash
curl -s http://localhost/health
```

The health route is implemented in `webhook_receiver/app.py`; it and `/webhooks/github` are the only paths the Caddy site proxies. If you have set `DASHBOARD_TOKEN`, open the dashboard at `http://127.0.0.1:8081/dashboard?token=…` — the receiver's loopback-only publish — because dashboard paths are `404`'d through the public `:80` site. Dashboard routes are intentionally unavailable when that token is unset; `docs/dashboard.md` covers tailnet access.

## Run the receiver without Compose

For a focused Python iteration:

```bash
uv sync --group dev
export OS_WEBHOOK_SECRET='…'
uv run orchestrator-webhook
```

`webhook_receiver/__main__.py` reads configuration, starts the optional Beads thread, and gives the FastAPI application to Uvicorn. A standalone receiver normally needs an accessible OpenCode server URL as well.

## Send an ad hoc prompt

With a local OpenCode CLI installed, the launcher can attach to a running server:

```bash
pwsh -NoProfile -File scripts/prompt.ps1 \
  -Prompt "Summarize open issues" \
  -ServerUrl http://localhost:4099
```

`scripts/prompt.ps1` resolves an isolated project workspace before calling `opencode run`; it does not install OpenCode or start a Docker client.

## Validate a change

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

`scripts/validate.ps1` runs Ruff, the changed-file secret scan, pytest with coverage, Pester, and shell/config checks. The CI-only image build job in `.github/workflows/validate.yml` adds functional image coverage.

For deployment options and operational caveats, see [Deployment](../deployment.md). For test layers and fixture conventions, see [Testing](../how-to-contribute/testing.md).
