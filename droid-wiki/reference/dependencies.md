# Dependencies

`orchestrator-service` ships as two application images (`orchestratorservice`, `webhook-receiver`) plus a Caddy proxy image, built from pinned base images and CLI tarballs, running a small pinned set of Python packages, and talking to a handful of external services (GitHub, several LLM/MCP providers, and GHCR). This page catalogs each layer with its source of truth.

## Source files

| Path | What it contributes |
| --- | --- |
| `pyproject.toml` | Direct Python dependencies (runtime + dev groups), Python version floor, and tooling config (`ruff`, `pytest`, `coverage`). |
| `uv.lock` | Fully resolved/pinned versions for every direct and transitive Python package. |
| `Dockerfile` | Base image, build-time Rust compilation, and every CLI/runtime installed into the `orchestratorservice` image. |
| `Dockerfile.webhook` | Base image and installed CLIs/runtimes for the `webhook-receiver` image. |
| `image/.opencode/opencode.json` | MCP server definitions and the configured model-provider catalog. |
| `webhook_receiver/runner.py` | The `gh` CLI integration (issue-state checks, issue comments) used to close the loop with GitHub. |
| `README.md` | Confirms the GHCR image coordinates and the Beads (`br`/`bvr`) DAG ecosystem this runtime is built around. |

## Python dependencies

Managed with `uv`; direct dependencies declared in `pyproject.toml`, fully pinned in `uv.lock`. `requires-python = ">=3.11"`.

### Runtime (`[project.dependencies]`)

| Package | Constraint | Locked version | Role |
| --- | --- | --- | --- |
| `fastapi` | `>=0.136.3` | `0.136.3` | The `webhook_receiver` HTTP application (routes, dependency injection, OpenAPI generation). |
| `jinja2` | `>=3.1` | `3.1.6` | Renders `orchestration_prompt.jinja2.md` into the dispatched orchestrator prompt. |
| `uvicorn[standard]` | `>=0.48.0` | `0.48.0` | ASGI server that runs the FastAPI app (`orchestrator-webhook` entry point). |

### Dev (`[dependency-groups.dev]`)

| Package | Constraint | Locked version | Role |
| --- | --- | --- | --- |
| `pytest` | `>=8` | `9.0.3` | Test runner (`tests/`). |
| `pytest-cov` | `>=6` | `7.1.0` | Coverage reporting (`--cov=webhook_receiver`, `htmlcov/`, `coverage.xml`). |
| `httpx` | `>=0.27` | `0.28.1` | Test client for FastAPI endpoint tests. |
| `ruff` | `>=0.9` | `0.15.15` | Linting (`E`, `F`, `I`, `UP` rule sets, `line-length=100`). |

### Notable transitive dependencies (from `uv.lock`)

| Package | Version | Pulled in by |
| --- | --- | --- |
| `starlette` | `1.3.1` | `fastapi` (ASGI toolkit underneath it) |
| `pydantic` / `pydantic-core` | `2.13.4` / `2.46.4` | `fastapi` (request/response validation) |
| `anyio` | `4.13.0` | `starlette`/`httpx` (async concurrency abstraction) |
| `uvloop` | `0.22.1` | `uvicorn[standard]` (fast event loop) |
| `httptools` | `0.8.0` | `uvicorn[standard]` (HTTP parsing) |
| `watchfiles` | `1.2.0` | `uvicorn[standard]` (reload support) |
| `websockets` | `16.0` | `uvicorn[standard]` |
| `python-dotenv` | `1.2.2` | `uvicorn[standard]` |
| `h11` / `httpcore` | `0.16.0` / `1.0.9` | `httpx` |

Build backend: `hatchling` (`[build-system]`), packaging `webhook_receiver` as the wheel's sole package.

## Container base images and installed CLIs

Both application Dockerfiles use the same two-stage pattern: a Rust builder stage that compiles the Beads ecosystem CLIs, and a Debian-based final stage.

| Image / stage | Used in | Purpose |
| --- | --- | --- |
| `rust:1.95-slim-bookworm` (stage `rust-builder`) | Both Dockerfiles | Compiles `br` (and, in `Dockerfile.webhook`, also `bvr`) from source via `cargo install --git`, pinned to immutable commit SHAs. Uses the Rust **nightly** toolchain (required by a `beads_rust` v0.2.15 transitive dependency's `#![feature]` usage). Overridden in CI (`docker-publish.yml`) with a prebuilt GHCR beads image to skip recompilation. |
| `debian:trixie-20260518-slim` (final stage) | Both Dockerfiles | The runtime base for both application images. |

### Installed CLIs and runtimes — `orchestratorservice` (`Dockerfile`)

| Tool | Version | Install method | Purpose |
| --- | --- | --- | --- |
| `opencode` | `1.18.4` | Vendor install script, binary copied to `/usr/local/bin` | The OpenCode CLI; runs `opencode serve` as the container's main process. |
| `pwsh` (PowerShell) | `7.6.2` | linux-x64 tarball (Microsoft's apt repo fails on trixie's SHA1 key policy) | Runs `scripts/*.ps1` inside the container when needed. |
| Node.js | `24.14.0` LTS | linux-x64 tarball | Required by MCP server packages launched via `npx` (`sequential-thinking`, `memory-graph`). |
| `uv` | `0.10.9` | Astral install script | Python package manager; enables `uvx` for ephemeral Python tools. |
| `br` (Beads CLI) | v0.2.15 (`beads_rust` @ `d9f8d7083dee46d04a8e4741c5f535eb7fcabc97`) | Copied from the `rust-builder` stage | DAG task selection/closure for the Beads pipeline. |
| `gh` (GitHub CLI) | latest from GitHub's apt repo | apt with keyring | Issue/PR/repo operations, authenticated via `GH_ORCHESTRATION_AGENT_TOKEN`/`GITHUB_TOKEN`. |
| `git`, `gosu`, `jq`, `make`, `openssh-client`, `patch`, `procps`, `python3`, `python3-pip`, `ripgrep`, `tar`, `unzip`, `xz-utils`, `ca-certificates`, `curl`, `file`, `gnupg` | apt (Debian trixie) | apt | General-purpose system tools available to agent sessions and the entrypoint. |

### Installed CLIs and runtimes — `webhook-receiver` (`Dockerfile.webhook`)

| Tool | Version | Install method | Purpose |
| --- | --- | --- | --- |
| `opencode` | `1.18.4` | Vendor install script | The **client** used by `scripts/prompt.ps1` for `opencode run --attach`. |
| `pwsh` (PowerShell) | `7.6.2` | linux-x64 tarball | Runs `scripts/prompt.ps1`, the dispatch launcher invoked by `runner.py`. |
| `uv` | `0.10.9` | Astral install script | Installs the pinned Python deps (`uv sync --frozen --no-dev`) and runs the app (`uv run orchestrator-webhook`). |
| `br` and `bvr` (Beads CLI + viewer) | `br` v0.2.15, `bvr` v0.2.1 (`beads_viewer_rust` @ `e4506f63214d32c8bcac4f29479a9b80cb932a6a`) | Copied from the `rust-builder` stage | `br` for task closure checks; `bvr` for graph-aware next-bead selection and the dashboard's pages/graph export. |
| `gh` (GitHub CLI) | latest from GitHub's apt repo | apt with keyring | Used by `runner.py` (`_gh_env`, `_post_issue_comment`, `_dispatch_issue_closed`) to check issue state and post run-outcome comments. |
| `git`, `gosu`, `python3`, `libicu76`, `libssl3t64`, `ca-certificates`, `curl`, `gnupg` | apt (Debian trixie) | apt | Runtime support (git identity is pre-configured for `orchestrator-bot`; `libicu76`/`libssl3t64` are `pwsh` runtime dependencies). |

## OpenCode-level dependencies (`image/.opencode/opencode.json`)

### MCP servers

| Server | Type | Endpoint / package | Role |
| --- | --- | --- | --- |
| `sequential-thinking` | local | `npx @modelcontextprotocol/server-sequential-thinking` | Structured step-by-step reasoning tool, mandated by `image/.opencode/AGENTS.md` for non-trivial tasks. |
| `memory-graph` | local | `npx @modelcontextprotocol/server-memory` | Persistent knowledge-graph MCP; persists to `/app/.memory/memory.jsonl` (single-writer: orchestrator only). |
| `web-reader` | remote | `https://api.z.ai/api/mcp/web_reader/mcp` | Fetches and converts a URL to model-friendly text/markdown. |
| `zread` | remote | `https://api.z.ai/api/mcp/zread/mcp` | Reads public GitHub repositories without cloning. |
| `web-search-prime` | remote | `https://api.z.ai/api/mcp/web_search_prime/mcp` | Structured web search. |
| `exa` | remote, `enabled: true` | `https://mcp.exa.ai/mcp` | Neural web search, code-context lookup, and site crawling (tools: `web_search_exa`, `web_fetch_exa`, `web_search_advanced_exa`, `get_code_context_exa`, `crawling_exa`). |

The three Z.AI remote servers authenticate with `ZAI_CODING_API_KEY`; `exa` authenticates with `EXA_API_KEY`. See [Configuration](configuration.md#model-provider-credential-categories).

### Model providers

| Provider | Kind | Models configured | Credential |
| --- | --- | --- | --- |
| `zai-coding-plan` | built-in | `glm-5.2`, `glm-4.7`, `glm-4.5-air`, `glm-5` (session default), `glm-5.1` | `auth.json` (resolved at container start); env fallback `ZAI_CODING_PLAN_OPEN_AI_API_KEY` |
| `opencode-go` | built-in | `qwen3.7-max` | `auth.json`; env fallback `OPENCODE_GO_API_KEY` (the entrypoint does not provision this one — env-only) |
| `google` | built-in | `gemini-3.5-flash` | `GEMINI_API_KEY` (read directly from env) |
| `nvidia` | built-in, custom `baseURL` | `minimaxai/minimax-m3` | `NVIDIA_NIM_API_KEY` / `NVIDIA_NIM_BASE_URL` |
| `cline-pass` | `@ai-sdk/openai`, OpenAI-compatible | `cline-pass/qwen3.7-max` | `CLINE_API_KEY` |
| `alibaba-model-studio` | `@ai-sdk/anthropic`, Anthropic-compatible | `qwen3.6-plus`, `qwen3.7-plus`, `qwen3.7-max-2026-05-20`, `qwen3.7-max-2026-05-17` | `MS_DS_API_KEY` |

## External services / integrations

| Service | Integration point | Purpose |
| --- | --- | --- |
| **GitHub** (App webhooks + REST API) | `webhook_receiver/app.py` (`POST /webhooks/github`), `gh` CLI in `runner.py` | Receives `issues.labeled` deliveries (HMAC-verified with `OS_WEBHOOK_SECRET`); posts run-outcome comments and checks dispatch-issue state via `gh issue comment`/`gh issue view`, authenticated with `GH_ORCHESTRATION_AGENT_TOKEN` (falling back to `GITHUB_TOKEN`). |
| **GHCR** (`ghcr.io/nam20485/orchestrator-service*`) | `compose.yaml`/`compose.development.yaml` image references, `pull_policy: always` | Hosts the pre-built `orchestratorservice`, `webhook-receiver`, and Caddy proxy images this stack pulls by default. |
| **Z.AI** (`api.z.ai`) | `zai-coding-plan` provider + `web-reader`/`zread`/`web-search-prime` MCP servers | Default GLM model access and three of the six configured MCP tools. |
| **Exa** (`mcp.exa.ai`) | `exa` MCP server | Neural search, code-context, and crawling tools. |
| **OpenRouter, Alibaba Model Studio/DashScope, Google AI Studio, NVIDIA NIM, ClinePass, OpenCode Go** | Alternate `provider` entries in `opencode.json` | Optional model providers, each used only when explicitly selected as the session/dispatch model. |
| **Let's Encrypt (ACME)** | `webhook-proxy` (Caddy), enabled via `compose.https.yaml` | Automatic TLS certificate issuance for a production hostname on `:443`. |

## Related pages

- [Configuration](configuration.md) lists the environment-variable categories that select and authenticate these providers/services.
- [Data models](data-models.md) documents the records the `gh` CLI integration produces and consumes (run manifest, dispatch context).
- [Architecture](../overview/architecture.md) shows where these dependencies sit in the request/execution flow.
