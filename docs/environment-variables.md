# Environment Variables

This is the canonical reference for every environment variable the **orchestrator-service**
runtime depends on. It supersedes the earlier template-clone-only description and now covers
both layers that ship in this repo:

1. **The docker-compose stack + shipped opencode config** — the running container.
   Credentials are injected by the `environment:` blocks in `compose.yaml` /
   `compose.development.yaml`, then:
   - MCP servers read keys directly from the container env via `{env:…}` placeholders in
     `image/.opencode/opencode.json` (the config the Dockerfile installs into the image).
   - Built-in model providers (`zai-coding-plan`, `openrouter`, `bailian-payg`) resolve
     credentials from `auth.json`, which `scripts/docker-entrypoint.sh` writes at startup
     from the env keys below (exits if none are present).
2. **Host-side automation scripts** — `scripts/*.ps1` (GitHub auth, label/index sync,
   permission checks). These run on the host or a downstream clone, **not** inside the
   container image.

> **Naming convention:** the Z.AI API key has a single canonical name — **`ZAI_CODING_API_KEY`**.
> It is what the shipped container config reads for the Z.AI MCP servers and what the
> entrypoint prefers for `auth.json`. `ZAI_API_KEY` is an accepted fallback (entrypoint +
> compose). The old `Z_AI_API_KEY` name is **not** consumed anywhere at runtime and should
> not be relied upon.

---

## Required — the stack will not function without these

| Variable | Used by | Purpose |
|---|---|---|
| `OPENCODE_SERVER_PASSWORD` | `compose.yaml` (both services) | Shared secret between the opencode server and clients/webhook-receiver. Fail-closed (`:?required`). |
| `OS_WEBHOOK_SECRET` | `webhook-receiver` | GitHub webhook HMAC verification secret. Required (fail-closed). |
| `WORKSPACE_DIR` | `compose.yaml` (bind mount) | Host directory bind-mounted to `/workspace` (agent session + beads working dir). Fail-closed (`:?required`). |
| `GH_ORCHESTRATION_AGENT_TOKEN` | `orchestratorservice` + `webhook-receiver` | Org-level PAT (`repo`, `workflow`, `project`, `read:org`) for agent `gh`/API calls. No fallback to `GITHUB_TOKEN`. |

## Required for default model access

The entrypoint needs **at least one** provider key to write `auth.json`, or it exits with an
error. The default model is `zai-coding-plan/glm-5`, so the primary key is:

| Variable | Used by | Purpose |
|---|---|---|
| `ZAI_CODING_API_KEY` | `docker-entrypoint.sh` → `auth.json` (`zai-coding-plan`); `image/.opencode/opencode.json` (Z.AI MCP servers) | Z.AI GLM model access **and** authentication for the `web-reader`/`zread`/`web-search-prime` MCP servers. |

Alternate/standalone provider keys (any one satisfies the entrypoint):

| Variable | Used by | Purpose |
|---|---|---|
| `ZAI_API_KEY` | `docker-entrypoint.sh` → `auth.json` (fallback for `zai-coding-plan`) | Accepted alternative to `ZAI_CODING_API_KEY`. |
| `OPENROUTER_API_KEY` | `docker-entrypoint.sh` → `auth.json` (`openrouter`) | OpenRouter provider access. |
| `MODEL_STUDIO_API_KEY` | `docker-entrypoint.sh` → `auth.json` (`bailian-payg`) | Alibaba Bailian pay-as-you-go access. |

## Required for the enabled MCP tools

The Exa MCP server is `enabled: true` in the shipped config and authenticates from env:

| Variable | Used by | Purpose |
|---|---|---|
| `EXA_API_KEY` | `image/.opencode/opencode.json` — Exa MCP | Sent as the `x-api-key` header on the Exa MCP server. Without it the enabled Exa server fails auth. |

---

## Model-provider keys consumed by the shipped config

These are read directly from the container env via `{env:…}` (or the built-in provider
convention). Only required when the corresponding provider/model is actually selected.

| Variable | Used by | Purpose |
|---|---|---|
| `QWENCLOUD_TOKEN_PLAN_API_KEY` | `qwencloud` provider | QwenCloud Token Plan (Anthropic-compatible endpoint). Required for the default model `qwencloud/qwen3.8-max`. |
| `ZAI_CODING_API_KEY` | Z.AI MCP servers + `zai-coding-plan` provider | Z.AI GLM models + MCP auth (see above). |
| `CLINE_API_KEY` | `cline-pass` provider | ClinePass subscription API key. Optional. |

---

## Compose operational variables

Injected into the container(s) by the compose `environment:` blocks (with defaults where shown).

| Variable | Service | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | `orchestratorservice` + `webhook-receiver` | Exported as `GH_TOKEN`/`GITHUB_TOKEN` so `gh`, MCP, and opencode authenticate. (CI-provided token is used for GHCR image push/pull.) |
| `NOTION_MCP_CONNECTIONS_API_KEY` | `orchestratorservice` | **No active consumer** in the shipped `image/.opencode` config — carried for a planned Notion MCP. Harmless if unset. |
| `DIRECT_BODY_ALLOWED_SENDERS` | `webhook-receiver` | Fail-closed allowlist (GitHub logins) gating `gh-issue-tracking:direct-body` dispatch. |
| `OPENCODE_SERVER_URL` | `webhook-receiver` | Hardcoded `http://orchestratorservice:4099`. |
| `OPENCODE_SERVER_LOG_PATH` | `webhook-receiver` | Server log path for the idle watchdog. Default `/var/log/opencode-server/opencode.log` (compose default; `config.py` code default is `/home/app/.local/share/opencode/log/opencode.log`, but compose mounts the shared server log over `/var/log/opencode-server`). |
| `DASHBOARD_TOKEN` | `webhook-receiver` | Shared secret gating the dashboard **and** simulator. When unset, the dashboard is disabled (404) and the simulator returns 401 when enabled. Independent of, and in addition to, the network restriction in `deploy/caddy/Caddyfile` — see [dashboard Network access](dashboard.md#network-access). |
| `IMAGE_REF` | compose interpolation | Image tag suffix. Default `main`. |
| `WEBHOOK_LOG_DIR`, `WEBHOOK_SITE_ADDRESS` | compose interpolation | Runner-log bind mount and Caddy site address. See `compose.yaml` defaults. |

The full `webhook-receiver` runtime configuration (model selection, watchdog tuning, beads loop, trace filtering) is enumerated in the next section — all of it has code defaults in `webhook_receiver/config.py` and is optional to override.

---

## webhook-receiver runtime configuration

Every variable below is read by `webhook_receiver/config.py` (`Settings.from_env()`) or `webhook_receiver/filters.py`, and has a code default — none are required for the stack to start. Defaults shown are the code defaults (`webhook_receiver/config.py:109-175`); the compose `environment:` blocks override a few of these for the in-container wiring.

### Dispatch / opencode session

| Variable | Default | Purpose |
|---|---|---|
| `OPENCODE_MODEL` | `zai-coding-plan/glm-5` | Model used for dispatched runs (passed as `--model`). |
| `OPENCODE_VARIANT` | `high` | Reasoning-effort variant passed via `--variant` (e.g. `low`/`medium`/`high`/`minimal`; empty string omits the flag). |
| `OPENCODE_AGENT` | `orchestrator` | Agent passed as `--agent`. |
| `ORCHESTRATOR_WORKSPACE` | `/workspace` | `--dir` passed to `opencode run`. |
| `PROMPT_SCRIPT` | `scripts/prompt.ps1` | PowerShell prompt launcher (requires `pwsh`). |
| `WEBHOOK_MAX_PAYLOAD_CHARS` | `120000` | Max JSON chars embedded in the rendered prompt. |
| `WEBHOOK_MAX_BODY_BYTES` | `26214400` (25 MiB) | Reject webhook POST bodies larger than this. |

### HTTP bind / simulator

| Variable | Default | Purpose |
|---|---|---|
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | `0.0.0.0` / `8080` | HTTP bind **inside** the receiver container. The compose publish to the host is loopback-only (`127.0.0.1:8081` → `8080`); Caddy's public `:80` site proxies only `/webhooks/github` and `/health`. |
| `WEBHOOK_LOG_LEVEL` | `info` | Logging level. |
| `WEBHOOK_ENABLE_SIMULATOR` | *(unset → off)* | Serve the dev UI at `/simulator` when set to a truthy value. Also requires `DASHBOARD_TOKEN`. |

### Beads loop

| Variable | Default | Purpose |
|---|---|---|
| `BEADS_ENABLED` | `true` | Enable the `BeadsLoop` background execution thread. |
| `BEADS_POLL_INTERVAL` | `10` | Seconds between bead-selection polls. |
| `BEADS_MAX_RETRIES` | `3` | Max retries per bead before halting for human intervention. |
| `BEADS_WORKSPACE_ROOT` | `/workspace` | Base directory containing per-project workspaces (`/workspace/<slug>/`). |

### Idle watchdog (`watchdog.py`)

A run is killed if it (a) produces no stdout/stderr for `IDLE_TIMEOUT_SECS`, (b) emits `MAX_CONSECUTIVE_ERRORS` error lines without a non-error line, or (c) exceeds `HARD_CEILING_SECS` regardless of activity. The opencode server log is tracked as an activity signal via `OPENCODE_SERVER_LOG_PATH`.

| Variable | Default | Purpose |
|---|---|---|
| `IDLE_TIMEOUT_SECS` | `900` | Max stdout/stderr silence before the run is killed. |
| `ERROR_GRACE_SECS` | `300` | Grace window for consecutive errors. |
| `HARD_CEILING_SECS` | `5400` | Absolute wall-clock safety net (falls back to `DISPATCH_TIMEOUT_SECS` if set). |
| `DISPATCH_TIMEOUT_SECS` | *(unset)* | Legacy wall-clock timeout; kept for backward compat, feeds `HARD_CEILING_SECS`. |
| `WATCHDOG_POLL_SECS` | `30` | Seconds between watchdog poll intervals. |
| `MAX_CONSECUTIVE_ERRORS` | `5` | Consecutive error lines (without a non-error line) that trigger a kill. |
| `PERMISSION_ASK_GRACE_SECS` | `60` | Grace before an unanswered permission `ask` in the server log is treated as a fatal headless deadlock. |
| `WATCHDOG_DEBUG` | *(unset → off)* | Enable verbose watchdog logging. |

### Trace filtering (`filters.py`)

| Variable | Default | Purpose |
|---|---|---|
| `TRACE_BLACKLIST_PATTERNS` | *(unset → built-ins)* | Newline-separated regex patterns to drop from run-log noise. When unset, the built-in default set in `filters.py` is used (high-frequency, zero-signal opencode lines); ERROR/WARN lines are always kept. |

---

## Host-side automation scripts (`scripts/*.ps1`)

These run on the host or a downstream clone, **not** inside the container image.

| Variable | Used by | Purpose |
|---|---|---|
| `GITHUB_AUTH_TOKEN` | `scripts/gh-auth.ps1`, `scripts/test-github-permissions.ps1` | Primary GitHub auth token for repo automation scripts. |
| `GITHUB_TOKEN` | `scripts/sync-agent-instruction-indices.ps1` | Fallback GitHub token for index sync (accepted wherever `GITHUB_AUTH_TOKEN` is expected). |
| `GITHUB_USERNAME` | `scripts/test-github-permissions.ps1` | Default repository owner for permission checks (`-Owner`). If unset the script warns and skips the project-creation test. |

> **Note:** `scripts/update-remote-indices.ps1` (referenced by older docs as the
> `GITHUB_TOKEN` fallback consumer) is **not present** in this repo; the real fallback
> consumer here is `scripts/sync-agent-instruction-indices.ps1`.

---

## Provider credential fallbacks (only when `auth.json` is absent)

The two built-in providers below resolve credentials from `auth.json` first and fall back to
these env vars. Since the entrypoint always writes `auth.json` (provisioning
`zai-coding-plan`, `openrouter`, `bailian-payg`), these are rarely needed.

| Variable | Used by | Purpose |
|---|---|---|
| `ZAI_CODING_PLAN_OPEN_AI_API_KEY` | `zai-coding-plan` provider | Fallback API key when `auth.json` is absent. |

---

## Do NOT set — internal runtime state

| Variable | Owner | Notes |
|---|---|---|
| `GHIT_LOG_FILE` | `gh-issue-tracking-init` skill | Set at runtime by the skill's `common.ps1` to carry the active log-file path between dot-sourced scripts. Not a secret; pre-defining it can interfere with log management. |

---

## Current environment status (host shell)

Checked via `echo $VAR`. Status reflects where compose interpolates `${VAR}` from.

| Variable | Status |
|---|---|
| `EXA_API_KEY`, `ZAI_CODING_API_KEY`, `ZAI_API_KEY`, `OPENROUTER_API_KEY`, `MODEL_STUDIO_API_KEY`, `QWENCLOUD_TOKEN_PLAN_API_KEY`, `NOTION_MCP_CONNECTIONS_API_KEY`, `CLINE_API_KEY`, `GH_ORCHESTRATION_AGENT_TOKEN`, `GITHUB_TOKEN`, `OPENCODE_SERVER_PASSWORD`, `ZAI_CODING_PLAN_OPEN_AI_API_KEY` | **SET** |
| `OS_WEBHOOK_SECRET`, `WORKSPACE_DIR`, `DIRECT_BODY_ALLOWED_SENDERS` | Define in `.env` / shell before `docker compose up` |
| `GITHUB_AUTH_TOKEN`, `GITHUB_USERNAME` | **UNSET** — only needed for host-side `scripts/*.ps1`; not consumed by the container. See "Host-side automation scripts" above. |

---

## Quick-start minimum set

For a running stack with the default model + enabled MCP tools, define:

```sh
# Stack secrets (required, fail-closed)
export OPENCODE_SERVER_PASSWORD="..."
export OS_WEBHOOK_SECRET="..."
export WORKSPACE_DIR="/path/to/orchestrator-workspace"
export GH_ORCHESTRATION_AGENT_TOKEN="ghp_..."   # org-level PAT

# Default model + Z.AI MCP servers
export ZAI_CODING_API_KEY="..."

# Enabled MCP (Exa)
export EXA_API_KEY="..."

# GitHub (container runtime)
export GITHUB_TOKEN="ghp_..."
```

For host-side automation scripts additionally:

```sh
export GITHUB_AUTH_TOKEN="ghp_..."   # or GITHUB_TOKEN
export GITHUB_USERNAME="..."
```
