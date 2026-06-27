# Deployment Guide — Compose / Caddy Stack

This documents the deployment we **actually have today**: a three-container Docker Compose stack fronted by Caddy. It covers the compose files, how they layer, local dev, how images are published, secrets, TLS, and the single-host production path — plus the limits of this approach.

> For a comparison of deployment options (including Kubernetes via IaC) and recommendations, see [`docs/deployment-options.md`](deployment-options.md).

## TL;DR

| Goal | Command |
|------|---------|
| Local dev (pull dev images) | `docker compose -f compose.development.yaml up -d` |
| Local dev (build from source) | `docker compose -f compose.development.yaml -f compose.build.yaml up -d --build` |
| Single-host prod (HTTPS) | `COMPOSE_FILE=compose.yaml:compose.https.yaml docker compose up -d` |

Two env vars are always required: `WORKSPACE_DIR` (host dir → `/workspace`) and `OPENCODE_SERVER_PASSWORD` (shared secret between the opencode server and its clients).

---

## The services

| Service | Image | Port | Role |
|---------|-------|------|------|
| `orchestratorservice` | `…/orchestrator-service` | `4099` | OpenCode server (`opencode serve`) hosting agent sessions |
| `webhook-receiver` | `…/orchestrator-service/webhook` | `8080` *(internal)* | FastAPI app: validates GitHub webhooks, runs the `BeadsLoop`, serves the dashboard + `/health` |
| `webhook-proxy` | `…/orchestrator-service/caddy` | `80` / `443` | Caddy reverse proxy (TLS edge) |

`orchestratorservice` and `webhook-receiver` **share** the same host directory via a bind mount at `/workspace` (`WORKSPACE_DIR`) — that's where agent sessions run and `.beads/` DAG state lives.

---

## The compose files

There are **four** compose files. They are either standalone or layered overlays.

### `compose.yaml` — base (production)
- Pulls `:main-latest` images from GHCR (`pull_policy: always`).
- Publishes host `:80` (HTTP). Add `compose.https.yaml` for `:443`.
- Requires `WORKSPACE_DIR` and `OPENCODE_SERVER_PASSWORD`.

### `compose.development.yaml` — standalone dev
- Pulls `:development-latest` images from GHCR.
- **Standalone** (it is *not* layered on `compose.yaml`) — it re-declares every service and environment variable.
- This is what the dev stack runs from. If you change an env var, change it **here** for dev (and in `compose.yaml` for prod).

### `compose.https.yaml` — TLS overlay
- Adds host `443:443` to `webhook-proxy` so Caddy terminates TLS with automatic Let's Encrypt certs.
- Layered on `compose.yaml`: `docker compose -f compose.yaml -f compose.https.yaml up`.
- **Do not** combine with Tailscale Funnel — Funnel also binds host `:443`.

### `compose.build.yaml` — local-build overlay
- Adds `build:` contexts and sets `pull_policy: never` so `up` uses images you build locally instead of pulling.
- Layered on `compose.development.yaml` for local image development:
  `docker compose -f compose.development.yaml -f compose.build.yaml up -d --build`.
- Without this overlay, neither base file builds from source — they only pull pre-built images.

### Layering rules

- Compose merges files left-to-right; later files override/extend earlier ones.
- Dev stack: **`compose.development.yaml` alone** (it's self-contained).
- Prod stack: **`compose.yaml` + `compose.https.yaml`**.
- Set once for prod: `export COMPOSE_FILE=compose.yaml:compose.https.yaml`.
- Build-from-source dev: **`compose.development.yaml` + `compose.build.yaml`**.

> Gotcha: every standalone/overlay that re-declares a service must repeat any env var you rely on. Today `compose.development.yaml` duplicates the full `environment:` block, so a new variable (e.g. `DASHBOARD_TOKEN`) must be added to **both** `compose.yaml` and `compose.development.yaml`.

---

## Local development

```bash
# 1. Prerequisites
mkdir -p ~/orchestrator-workspace
export WORKSPACE_DIR=~/orchestrator-workspace

# 2. Required secrets (pick real values)
export OPENCODE_SERVER_PASSWORD='…'   # shared opencode server/client secret
export OS_WEBHOOK_SECRET='…'          # GitHub App webhook secret
export DASHBOARD_TOKEN='…'            # enables the dashboard UI/APIs
# Provider keys for the agent (at least one):
export ZAI_CODING_API_KEY='...'
# Optional:
export GH_ORCHESTRATION_AGENT_TOKEN='ghp_...'      # PAT for gh in the agent
export GITHUB_TOKEN='ghp_...'

# 3a. Run from published dev images (fastest)
docker compose -f compose.development.yaml up -d

# 3b. Or build from source (use when editing Dockerfiles / app code baked into images)
docker compose -f compose.development.yaml -f compose.build.yaml up -d --build
```

Verify:

```bash
curl -s http://localhost/health                                  # -> {"status":"ok"}
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" \
     http://localhost/api/dashboard/overview | jq                 # -> counts
# UI (sets a cookie) — use the opener for your platform:
#   macOS:  open "http://localhost/dashboard?token=$DASHBOARD_TOKEN"
#   Linux:  xdg-open "http://localhost/dashboard?token=$DASHBOARD_TOKEN"
xdg-open "http://localhost/dashboard?token=$DASHBOARD_TOKEN"
```

### Publishing webhooks to local

The receiver is behind Caddy on host `:80`. For local GitHub webhook development, tunnel public HTTPS to host **80** (not `8080`):

- **Tailscale Funnel** (recommended, stable `*.ts.net` URL): `tailscale funnel 80`. Use `compose.yaml` only — **no** `compose.https.yaml`.
- **ngrok**: `ngrok http 80`.

Point the GitHub App webhook at `https://<tunnel-host>/webhooks/github`.

### Quick iteration on the dashboard UI

The dashboard HTML is **baked into the `webhook` image** (no source mount). To preview a `dashboard.html` change without a full rebuild, rebuild just the receiver:

```bash
docker compose -f compose.development.yaml -f compose.build.yaml build webhook-receiver
docker compose -f compose.development.yaml -f compose.build.yaml up -d --force-recreate webhook-receiver
```

The Rust builder stage is cached, so an HTML-only change rebuilds in seconds.

---

## How images are published

[`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml) builds and pushes images on:

- push to `main` or `development` (→ `<branch>-latest` + `<branch>-<run_number>` tags),
- `v*.*.*` tags.

| Image | Dockerfile | GHCR path |
|-------|-----------|-----------|
| orchestrator-service | `Dockerfile` | `ghcr.io/<org>/<repo>` |
| webhook | `Dockerfile.webhook` | `ghcr.io/<org>/<repo>/webhook` |
| caddy | `deploy/caddy/Dockerfile` | `ghcr.io/<org>/<repo>/caddy` |
| beads (builder) | `Dockerfile.beads` | `ghcr.io/<org>/<repo>/beads` |

- Runtime images use a **published beads builder image** (`build-contexts: rust-builder=...`) so Rust isn't recompiled per image.
- All images are **cosign-signed** and pushed to GHCR; builds use GHA layer caching per matrix entry.
- `compose.yaml` → `:main-latest`; `compose.development.yaml` → `:development-latest`.

So promotion is simply: merge to `main` (or tag a release) → workflow publishes → `pull_policy: always` picks it up on the next `up`.

---

## Secrets

- **No secrets are committed.** The project deliberately does not use a checked-in `.env` for production; provider credentials and tokens are **host/CI environment variables** passed into containers at start.
- A local `.env` (gitignored) exists only for compose interpolation (`WORKSPACE_DIR`, dev passwords). A **shell-exported value overrides** `.env` — so set real secrets in your shell for `up`.
- Required secrets in compose:

| Variable | Used by | Notes |
|----------|---------|-------|
| `OPENCODE_SERVER_PASSWORD` | server + clients | `${…:?…}` — compose fails if unset |
| `OS_WEBHOOK_SECRET` | webhook-receiver | GitHub App webhook HMAC secret |
| `DASHBOARD_TOKEN` | webhook-receiver | Dashboard disabled (404) if unset |
| `ZAI_CODING_API_KEY` / `OPENROUTER_API_KEY` / `MODEL_STUDIO_API_KEY` | orchestratorservice | Provider auth for the agent |
| `GH_ORCHESTRATION_AGENT_TOKEN` / `GITHUB_TOKEN` | orchestratorservice | PAT for `gh`/API in the agent |

> Pre-commit safety: `scripts/validate.ps1 -Scan` (or the `scan-uncommitted-secrets` skill) rejects real keys (`ghp_`, `sk-`, `AKIA`, …). Test fixtures use `FAKE-KEY-FOR-TESTING-…` only.

---

## TLS / HTTPS

| Scenario | Edge config |
|----------|-------------|
| Local + tunnel (Funnel/ngrok) | `compose.yaml` only, `:80`. TLS terminates upstream. |
| Production (own domain) | `COMPOSE_FILE=compose.yaml:compose.https.yaml` + `WEBHOOK_SITE_ADDRESS=hooks.example.com` → Caddy automatic Let's Encrypt on `:443`. |

DNS for `WEBHOOK_SITE_ADDRESS` must point at the host. Optionally add a `global { email you@example.com }` block in `deploy/caddy/Caddyfile` for ACME account email.

---

## Single-host production

```bash
# On the host (one node)
export WORKSPACE_DIR=/srv/orchestrator-workspace
export OPENCODE_SERVER_PASSWORD='…'
export OS_WEBHOOK_SECRET='…'
export DASHBOARD_TOKEN='…'
export ZAI_CODING_API_KEY='...'
export GH_ORCHESTRATION_AGENT_TOKEN='<pat>'
export WEBHOOK_SITE_ADDRESS=hooks.example.com

COMPOSE_FILE=compose.yaml:compose.https.yaml docker compose up -d
```

Operational notes:
- `restart: always`/`unless-stopped` keeps containers up across reboots.
- `/workspace` and `opencode-memory` are persistent volumes/bind mounts — **back them up**. They hold agent working trees and `.beads/` DAG state.
- Health check: `curl -s http://localhost:80/health` (through Caddy) or `:8080/health` direct.
- Updates: push/merge to `main` → workflow republishes `:main-latest` → `docker compose pull && docker compose up -d`. There is **no** rolling deploy; `up -d` recreates containers (brief downtime on that container).

---

## Limits of this stack

This is a single-host deployment. It does **not** provide:

- **High availability / scaling** — one Caddy, one of each service, one `/workspace`. No horizontal scaling.
- **Rolling / zero-downtime deploys** — `up -d` recreates containers; expect brief per-container downtime.
- **Multi-environment promotion** — no first-class staging; you'd run the same compose on another host.
- **Orchestration resilience** — if a container dies, Docker restarts it, but there's no scheduler rebalancing, no health-gated rollout, no autoscaling.
- **Stateless scaling** — `EventStore` is in-process; bead state is on disk under `/workspace`. Both pin you to one node.

These gaps are what a Kubernetes (+ Terraform/Pulumi) path would address. See [`docs/deployment-options.md`](deployment-options.md) for the trade-off analysis and recommendations.
