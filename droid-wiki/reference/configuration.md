# Configuration

`orchestrator-service` is configured through three layers: the Compose `environment:` blocks that populate the container environment, the frozen `Settings` dataclass in `webhook_receiver/config.py` (`Settings.from_env()`) that reads it, and the checked-in OpenCode config tree `image/.opencode/opencode.json` that ships into the image at build time. Docker build `ARG`s configure a fourth, build-time-only layer.

This page catalogs configuration **categories and defaults** — not secret values or the live status of any host/CI environment. For the exhaustive, value-by-value canonical reference (every variable, its consumer, and its default) see `docs/environment-variables.md`.

## Source files

| Path | What it contributes |
| --- | --- |
| `compose.yaml` | Base production stack: service topology, required/optional env wiring, volumes, hardening (`security_opt`, `cap_drop`/`cap_add`). |
| `compose.development.yaml` | Same topology pinned to `development-latest` image tags, without the production capability hardening. |
| `compose.https.yaml` | Overlay: publishes host `:443` for Caddy automatic HTTPS. |
| `compose.build.yaml` | Overlay: builds images from local `Dockerfile`/`Dockerfile.webhook`/`deploy/caddy/Dockerfile` instead of pulling from GHCR. |
| `webhook_receiver/config.py` | The `Settings` dataclass and `Settings.from_env()` — the code-level defaults for every webhook-receiver runtime knob. |
| `image/.opencode/opencode.json` | OpenCode server config: default agent/model, per-agent variant overrides, MCP server definitions, permission mode, and the provider/model catalog. |
| `Dockerfile` | Build `ARG`s and installed-tool versions for the `orchestratorservice` image. |
| `Dockerfile.webhook` | Build `ARG`s and installed-tool versions for the `webhook-receiver` image. |
| `docs/environment-variables.md` | Canonical, exhaustive variable-by-variable reference (defaults, consumers, required status). |

## Fail-closed stack secrets

These four variables have **no code default**; the stack refuses to start (Compose `:?required`) or the process raises on boot without them. Values are never documented here — see `docs/environment-variables.md`.

| Variable | Enforced by | Consumers |
| --- | --- | --- |
| `OPENCODE_SERVER_PASSWORD` | `compose.yaml` / `compose.development.yaml` (`:?required`) | `orchestratorservice`, `webhook-receiver` |
| `WORKSPACE_DIR` | `compose.yaml` / `compose.development.yaml` (`:?required`) | Both services' `/workspace` bind mount |
| `OS_WEBHOOK_SECRET` | `Settings.from_env()` raises `ValueError` if blank | `webhook-receiver` (GitHub HMAC verification) |
| `GH_ORCHESTRATION_AGENT_TOKEN` | No fallback to `GITHUB_TOKEN` for agent `gh`/API calls | Both services |

## Model-provider credential categories

`compose.yaml`/`compose.development.yaml` pass these through to the container environment (values only — no defaults are applicable). At least one of the primary Z.AI keys must be present or the image's provider-auth bootstrap exits; see `docs/environment-variables.md` for the exact precedence and consumer detail.

| Category | Variables | Consumed by |
| --- | --- | --- |
| Primary/default model | `ZAI_CODING_API_KEY` (or fallback `ZAI_API_KEY`) | `zai-coding-plan` provider **and** the three Z.AI remote MCP servers in `image/.opencode/opencode.json` |
| Alternate providers | `OPENROUTER_API_KEY`, `MODEL_STUDIO_API_KEY`, `MS_DS_API_KEY`, `GEMINI_API_KEY`, `NVIDIA_NIM_API_KEY`/`NVIDIA_NIM_BASE_URL`, `CLINE_API_KEY` | Their respective providers in `image/.opencode/opencode.json` |
| Enabled MCP tools | `EXA_API_KEY` | The `exa` MCP server (`enabled: true` in `opencode.json`) |
| GitHub | `GH_ORCHESTRATION_AGENT_TOKEN`, `GITHUB_TOKEN` | `gh` CLI, agent GitHub API calls, GHCR pull/push |

## webhook-receiver runtime settings (`webhook_receiver/config.py`)

Every field below has a code default and is optional to override. `Settings.from_env()` reads the corresponding env var (see the `from_env` classmethod).

### Dispatch / opencode session

| Field | Env var | Code default |
| --- | --- | --- |
| `opencode_server_url` | `OPENCODE_SERVER_URL` | `http://localhost:4099` |
| `prompt_script` | `PROMPT_SCRIPT` | `<repo_root>/scripts/prompt.ps1` (resolved from `config.py`'s own path) |
| `workspace` | `ORCHESTRATOR_WORKSPACE` | `/workspace` |
| `model` | `OPENCODE_MODEL` | `zai-coding-plan/glm-5` |
| `variant` | `OPENCODE_VARIANT` | `high` (GLM-5's reasoning-effort ceiling is `high` — there is no `max`) |
| `agent` | `OPENCODE_AGENT` | `orchestrator` |
| `max_payload_chars` | `WEBHOOK_MAX_PAYLOAD_CHARS` | `120000` |
| `max_body_bytes` | `WEBHOOK_MAX_BODY_BYTES` | `26214400` (25 MiB — the GitHub webhook payload cap) |

### HTTP bind / simulator

| Field | Env var | Code default |
| --- | --- | --- |
| `host` | `WEBHOOK_HOST` | `0.0.0.0` |
| `port` | `WEBHOOK_PORT` | `8080` |
| `log_level` | `WEBHOOK_LOG_LEVEL` | `info` |
| `enable_simulator` | `WEBHOOK_ENABLE_SIMULATOR` | off (truthy values: `1`/`true`/`yes`) |

`host`/`port` are the **in-container** bind; they say nothing about host or network reachability. On the host, Compose publishes the app loopback-only at `127.0.0.1:8081`, and the Caddy site on `:80`/`:443` proxies only `/webhooks/github` and `/health` — so dashboard, dashboard-API, and simulator paths `404` through the public edge however these two settings are configured (`docs/dashboard.md`).

### Dashboard

| Field | Env var | Code default |
| --- | --- | --- |
| `dashboard_token` | `DASHBOARD_TOKEN` | `None` (dashboard and simulator disabled until set) |

### Beads loop

| Field | Env var | Code default |
| --- | --- | --- |
| `beads_enabled` | `BEADS_ENABLED` | `true` |
| `beads_poll_interval` | `BEADS_POLL_INTERVAL` | `10` (seconds) |
| `beads_max_retries` | `BEADS_MAX_RETRIES` | `3` |
| `beads_workspace_root` | `BEADS_WORKSPACE_ROOT` | `/workspace` |

### Idle watchdog

Full behavioral description in [Data models](data-models.md#watchdog-configuration-and-state).

| Field | Env var | Code default |
| --- | --- | --- |
| `dispatch_timeout` | `DISPATCH_TIMEOUT_SECS` | *(unset)* — legacy, feeds `hard_ceiling_secs` |
| `idle_timeout_secs` | `IDLE_TIMEOUT_SECS` | `900` |
| `error_grace_secs` | `ERROR_GRACE_SECS` | `300` |
| `hard_ceiling_secs` | `HARD_CEILING_SECS` | `5400` |
| `watchdog_poll_secs` | `WATCHDOG_POLL_SECS` | `30` |
| `max_consecutive_errors` | `MAX_CONSECUTIVE_ERRORS` | `5` |
| `permission_ask_grace_secs` | `PERMISSION_ASK_GRACE_SECS` | `60` |
| `watchdog_debug` | `WATCHDOG_DEBUG` | off |
| `server_log_path` | `OPENCODE_SERVER_LOG_PATH` | `/home/app/.local/share/opencode/log/opencode.log` |
| `log_dir` | *(none — derived)* | `{tempfile.gettempdir()}/orchestrator-webhook` |

### Trace filtering

`TRACE_BLACKLIST_PATTERNS` (newline-separated regexes) filters high-frequency, zero-signal lines out of container log output; unset uses `webhook_receiver/filters.py`'s built-in default set. ERROR/WARN lines are never filtered. (Field lives in `filters.py`, not the `Settings` dataclass; documented in `docs/environment-variables.md`.)

## Compose-level overrides of code defaults

Compose sets its own default in the `environment:` block for a few knobs, which differs from the `Settings` code default shown above — this is the *effective* default when running under Compose:

| Variable | Code default (`config.py`) | Compose default (`compose.yaml`/`compose.development.yaml`) | Why they differ |
| --- | --- | --- | --- |
| `IDLE_TIMEOUT_SECS` | `900` | `1800` | Subagent delegations routinely exceed 15 minutes; raised so the client-idle signal doesn't false-positive during active delegation. |
| `DISPATCH_TIMEOUT_SECS` | *(unset)* | `2700` | Legacy wall-clock timeout kept as a Compose-level default; feeds `hard_ceiling_secs` when `HARD_CEILING_SECS` is unset. |
| `OPENCODE_SERVER_LOG_PATH` | `/home/app/.local/share/opencode/log/opencode.log` | `/var/log/opencode-server/opencode.log` | The `webhook-receiver` container mounts the shared server log read-only at a different path than the writable path used inside `orchestratorservice` (its own client log occupies the code-default path). |
| `opencode_server_url` | `http://localhost:4099` | `http://orchestratorservice:4099` | Compose resolves the server by its service DNS name rather than `localhost`. |

## Compose stack files

| File | Role |
| --- | --- |
| `compose.yaml` | Production base: `orchestratorservice`, `webhook-receiver`, `webhook-proxy`, pinned to `${IMAGE_REF:-main}-latest` GHCR tags, hardened (`security_opt: no-new-privileges`, `cap_drop: ALL` + minimal `cap_add`). |
| `compose.development.yaml` | Same three-service topology pinned to `development-latest` tags, without the production capability hardening. |
| `compose.https.yaml` | Overlay adding host `:443` for Caddy automatic HTTPS (Let's Encrypt) — used only when nothing else (e.g. Tailscale Funnel) already binds host `:443`. |
| `compose.build.yaml` | Overlay: builds `orchestratorservice`/`webhook-receiver`/`webhook-proxy` from local Dockerfiles (`pull_policy: never`) instead of pulling published images. |

## Volumes and mounts

| Name | Mount | Services | Purpose |
| --- | --- | --- | --- |
| `${WORKSPACE_DIR}` (bind) | `/workspace` | `orchestratorservice`, `webhook-receiver` | Shared agent working directory: downstream repo clones, `.beads/` DAGs, per-bead worktrees. |
| `${WEBHOOK_LOG_DIR:-./traces/runner}` (bind) | `/tmp/orchestrator-webhook` | `webhook-receiver` | Persists per-run prompt/stdout/stderr files, run manifests, and `webhooks.json` (see [Data models](data-models.md)). |
| `opencode-memory` (named) | `/app/.memory` | `orchestratorservice` | Single-writer MCP memory-graph store (`memory.jsonl`). |
| `opencode-logs` (named) | `/home/app/.local/share/opencode/log` (rw, `orchestratorservice`) / `/var/log/opencode-server` (ro, `webhook-receiver`) | Both | Shares the OpenCode server log so the watchdog can observe server-side activity during subagent delegation. |
| `caddy_data`, `caddy_config` (named) | `/data`, `/config` | `webhook-proxy` | Caddy's ACME certificate/state and config persistence. |

## Docker build arguments

| ARG | Dockerfile(s) | Default | Purpose |
| --- | --- | --- | --- |
| `OPENCODE_VERSION` | `Dockerfile`, `Dockerfile.webhook` | `1.18.4` | Pinned OpenCode CLI version installed into both images. |
| `NODE_LTS_VERSION` | `Dockerfile` only | `24.14.0` | Node.js tarball install (MCP server packages run via `npx`). |
| `POWERSHELL_VERSION` | `Dockerfile`, `Dockerfile.webhook` | `7.6.2` | `pwsh` tarball install (runs `scripts/*.ps1`). |
| `APP_UID` / `APP_GID` | `Dockerfile`, `Dockerfile.webhook` | `1000` / `1000` | Baked-in non-root `app` user; rebuild with different values to match a non-1000 host UID. |
| `DEBIAN_FRONTEND` | `Dockerfile` | `noninteractive` | Suppresses `apt-get` interactive prompts during build. |

## OpenCode config (`image/.opencode/opencode.json`)

Non-secret settings baked into the image and installed to `/home/app/.config/opencode/` at build time:

| Setting | Value |
| --- | --- |
| `default_agent` | `orchestrator` |
| `model` | `zai-coding-plan/glm-5` |
| `small_model` | `zai-coding-plan/glm-4.5-air` |
| `permission` | `allow` (server-side; governs every session including task-spawned subagents) |
| Per-agent `variant` overrides | `high` for `developer`, `code-reviewer`, `debugger`, `documentation-expert`, `github-expert`, `odbplusplus-expert`, `planner`, `qa-test-engineer`, `researcher`, `security-expert`, `agent-instructions-expert` (the orchestrator itself is omitted — it gets `--variant high` from the dispatch CLI). |
| MCP servers | Local: `sequential-thinking`, `memory-graph`. Remote: `web-reader`, `zread`, `web-search-prime` (all `api.z.ai`), `exa` (`enabled: true`). See [Dependencies](dependencies.md) for the full catalog. |
| Model providers configured | `zai-coding-plan`, `opencode-go`, `google`, `nvidia`, `cline-pass`, `alibaba-model-studio`. See [Dependencies](dependencies.md) for the per-provider model list. |

## Related pages

- [Data models](data-models.md) documents the manifest, webhook-store, and watchdog structures shaped by the settings above.
- [Dependencies](dependencies.md) covers the MCP servers, model providers, and CLIs these settings select between.
- [Architecture](../overview/architecture.md) shows where each configuration layer sits in the runtime.
- [Getting started](../overview/getting-started.md) walks through the minimum variables needed to start the stack locally.
