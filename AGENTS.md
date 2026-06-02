## Learned User Preferences

- Wants repo-visible POR/spec documents (e.g. `plan_docs/plan.md`) with requirements, acceptance criteria, validation plan, and phased development—not Cursor-internal plan files alone for approval.
- Does not want RFC-style planning writeups; wants a formal spec the user can see, save, and approve before implementation.
- OpenCode server containers should be generic and workspace-agnostic; do not mount the host repo by default—clients, prompts, or launchers define code access via `--dir` (default `/workspace` in `scripts/prompt.ps1`).
- Client wrapper scripts (`scripts/prompt.ps1`, `scripts/attach.ps1`) should be thin pass-throughs that require a local `opencode` CLI; no auto-install or Docker client fallback in the first implementation.
- When the server is network-reachable (e.g. Tailscale/LAN), publish port `4099` and require `OPENCODE_SERVER_PASSWORD` at Compose startup.
- Does not use a project `.env` for compose; provider credentials are host/CI environment variables synthesized at container start by `scripts/docker-entrypoint.sh`.
- Client scripts should rely on host `OPENCODE_SERVER_PASSWORD` (opencode CLI default); never hardcode server passwords in committed scripts.
- Before commit, scan changed files for secrets (API keys, hardcoded passwords); never commit API keys or provider credentials.
- Use `.cursor/skills/scan-uncommitted-secrets` for pre-commit secret checks; use `.cursor/skills/safe-commit` for grouped commits after a clean scan.
- For webhook simulator secrets, avoid new config endpoints; read `OS_WEBHOOK_SECRET` from the server process environment and inject it when serving simulator HTML.
- When describing fixes, distinguish uncommitted local edits from committed repo state and from what a rebuilt/running container image actually contains.
- For local GitHub webhook development, tunnel public HTTPS to host port **80** (Caddy), not receiver **8080**; use **ngrok** or **Tailscale Funnel** (`tailscale funnel 80`; stable `*.ts.net` URL, less churn than free ngrok).

## Learned Workspace Facts

- Docker image uses `debian:trixie-20260518-slim`, runs `opencode serve` on `0.0.0.0:4099`, and bundles Node.js 24.14.0, pwsh 7.6.2 LTS, uv, gh CLI, Python3, ripgrep, jq, and agent utilities (git, make, openssh-client, gnupg, patch, xz-utils, file, procps); Node and pwsh install from linux-x64 `.tar.gz` tarballs (image is amd64-only; PowerShell uses GitHub tarball because Microsoft apt repo fails on trixie SHA1 policy).
- Authoritative OpenCode server POR: `plan_docs/plan.md`; dual orchestrator/maestro supervisor spec: `plan_docs/orchestration_supervisor.md`; maestro architecture options and recommendation: `plan_docs/maestro_architecture_options.md`.
- OpenCode server config source of truth is repo `image/` (`opencode.json`, `AGENTS.md`, `.opencode/agents/`, `.opencode/commands/`); Dockerfile copies those into `/app` (no full-repo `COPY . .`); `scripts/docker-entrypoint.sh` exports `OPENCODE_CONFIG=/app/opencode.json` and `OPENCODE_CONFIG_DIR=/app/.opencode` so `opencode serve` loads image config instead of defaulting to `~/.config/opencode`.
- Agent sessions run in `/workspace` (compose volume `opencode-workspace`); `/app` is server config only—keep working tree separate from OpenCode install/config.
- Root repo `AGENTS.md` is Cursor memory plus host-repo validation docs; the container uses `image/AGENTS.md` copied to `/app/AGENTS.md` (overwrites any root copy).
- Provider auth: `scripts/docker-entrypoint.sh` writes `/root/.local/share/opencode/auth.json` from host/CI env vars before `opencode serve` starts; supported vars include `ZAI_CODING_API_KEY` (or `ZAI_API_KEY`), `OPENROUTER_API_KEY`, and `MODEL_STUDIO_API_KEY`; Alibaba Model Studio Singapore (`bailian-payg`) defaults to `bailian-payg/qwen3.6-plus` with `bailian-payg/qwen3.6-flash` as `small_model`.
- `zai-coding-plan/glm-4.7` needs `ZAI_CODING_API_KEY`; `OPENROUTER_API_KEY` alone does not authenticate that provider.
- MCP `memory-graph` in `image/opencode.json` uses `@modelcontextprotocol/server-memory` with `MEMORY_FILE_PATH=/app/.memory/memory.jsonl`; compose volume `opencode-memory` persists it.
- OpenCode config: use `default_agent` (not `agent`); remote MCPs like `microsoft-learn` need `type: "remote"` and `enabled: true`.
- Compose `environment: - VAR` passes host shell env into the container; `${VAR}` adds `.env` interpolation—this project does not use `.env`.
- Host client scripts: `scripts/prompt.ps1`, `scripts/attach.ps1` (PowerShell thin wrappers to local `opencode`; pwsh is a host prerequisite); one-shot via `opencode run --attach <url>`, interactive via `opencode attach <url>`.
- GitHub webhook stack: `webhook_receiver/` FastAPI validates App webhooks with `OS_WEBHOOK_SECRET` and dispatches OpenCode via `scripts/prompt.ps1` (`-PromptFile` for large payloads); `webhook-receiver` (internal :8080) behind `webhook-proxy` (Caddy on host :80/:443); path `/webhooks/github`; dev simulator UI at `/simulator` when `WEBHOOK_ENABLE_SIMULATOR=1` (compose defaults off), with `OS_WEBHOOK_SECRET` injected server-side into non-cached HTML.

## Testing

- Full suite: `pwsh -NoProfile -File ./scripts/validate.ps1 -Test` (or `-All` for lint + scan + test).
- Python: `uv sync --group dev` then `uv run pytest tests/ -q`.
- Pester: `pwsh -NoProfile -File ./test/run-pester-tests.ps1`.
- Bash: `test/test-docker-entrypoint.sh`, `test/test-compose-config.sh`, `test/test-caddyfile.sh`, `test/test-opencode-json.sh`.
- Webhook fixtures: `test/fixtures/github/` (use `FAKE-KEY-FOR-TESTING-…` only; never `ghp_`, `sk-`, `AKIA`, etc. in fixtures).

## Change validation (mandatory)

After any non-trivial change (code, config, workflows, Docker):

1. Run `pwsh -NoProfile -File ./scripts/validate.ps1 -All`.
2. Fix all failures; re-run until clean.
3. Only then commit and push.

Missing local tools: `pwsh -NoProfile -File ./scripts/install-dev-tools.ps1`.

## Validation commands

| Check | Command | CI job (`validate.yml`) |
|-------|---------|-------------------------|
| All (local) | `./scripts/validate.ps1 -All` | lint + scan + test (not build) |
| Lint | `./scripts/validate.ps1 -Lint` | `lint` |
| Secret scan | `./scripts/validate.ps1 -Scan` | `scan` |
| Tests | `./scripts/validate.ps1 -Test` | `test` |
| Docker image build | *(CI only)* | `build` |

## Pre-commit checklist

- Ran `./scripts/validate.ps1 -All` (or the relevant `-Lint` / `-Scan` / `-Test` subset).
- Secret scan clean (`.cursor/skills/scan-uncommitted-secrets` or `validate.ps1 -Scan`).
- No real API keys or tokens in committed files.

## After push

Monitor CI until green: `gh run list --limit 5`, `gh run watch <id>`, `gh run view <id> --log-failed`. Required workflow: **validate** (lint, scan, test, build). Do not mark work complete while CI is red.

## Agent Instructions

- Bootstrap entry point for the agent instruction set: core module locations, remote loading protocol, and single-source-of-truth policy.
- Start with Core Instructions, then follow links to other modules as the request requires.

## Configuration

- **Branch:** `main` — change to load instructions from another branch (`optimization`, `feature/*`, or any valid branch name).
- Replace all `{branch}` placeholders in remote URLs with the configured branch value.

## Instruction Source

- **Repository:** `nam20485/agent-instructions` — `https://github.com/nam20485/agent-instructions/tree/{branch}`
- Remote URLs use the branch from Configuration above.
- The remote canonical repository is the **only** authoritative source for dynamic workflows and workflow assignments; fetch and execute from remote URLs, not local mirrors or cached plans.

## Module Registry

- **Core Instructions** (required): `https://github.com/nam20485/agent-instructions/blob/{branch}/ai_instruction_modules/ai-core-instructions.md` — foundational agent behaviors and rules.
- **Local AI Instructions** (required): `local_ai_instruction_modules/` — context-specific workspace instructions.
- **Dynamic Workflow Orchestration** (required): `local_ai_instruction_modules/ai-dynamic-workflows.md` — resolve workflows from the remote canonical repository.
- **Workflow Assignments** (required): `local_ai_instruction_modules/ai-workflow-assignments.md` — index of active workflow assignments by shortId.
- **Development Instructions** (required): `local_ai_instruction_modules/ai-development-instructions.md` — shell environment, architecture, tool preferences, and repo development rules.
- **Terminal Commands** (optional): `local_ai_instruction_modules/ai-terminal-commands.md` — terminal operations and GitHub CLI reference.

## Loading Protocol

- Read the configured branch from Configuration; if missing, use the repository default branch.
- Always fetch remote file contents via **raw** URLs — never use the GitHub UI URL.
- **URL translation:** `https://github.com/.../blob/{branch}/...` → `https://raw.githubusercontent.com/.../{branch}/...` (replace host, remove `blob/`, substitute `{branch}`).
- Example (`main`): `https://github.com/nam20485/agent-instructions/blob/main/ai_instruction_modules/ai-core-instructions.md` → `https://raw.githubusercontent.com/nam20485/agent-instructions/main/ai_instruction_modules/ai-core-instructions.md`

## Tool Use Instructions

### Querying Microsoft Documentation

- **Tools:** `microsoft_docs_search`, `microsoft_docs_fetch`, `microsoft_code_sample_search`
- Use for native Microsoft technologies (C#, F#, ASP.NET Core, Microsoft.Extensions, NuGet, Entity Framework, dotnet runtime).
- Prioritize retrieved documentation and code samples over training data, especially for newer features; check docs before writing Microsoft-related code.

### Sequential Thinking (default)

- **Tool:** `sequential_thinking`
- Use for all requests except the most trivial single-step tasks (minimal formatting, one-line lookups).
- Use when breaking down complex problems, planning design work, analyzing unclear scope, debugging systematically, making architectural decisions, or filtering irrelevant context.
- Supports revising prior thoughts, branching reasoning paths, and adjusting estimated step counts as complexity becomes clear.

### Knowledge Graph Memory (default)

- **Tools:** `create_entities`, `create_relations`, `add_observations`, `delete_entities`, `delete_observations`, `delete_relations`, `read_graph`, `search_nodes`, `open_nodes`
- Use for all requests except the most trivial single-step tasks; persist relevant user/project context for future continuity.
- Store user preferences, project patterns, technical decisions, recurring solutions, team context, tool/auth configuration, and environment setup details.
- **Entities** are named typed nodes with observations; **relations** are directed active-voice links; **observations** are atomic facts (one per observation).
- At task start, search or read relevant memory; after significant work, update memory with new patterns, configurations, or insights.
- Prefer `search_nodes` / `open_nodes` over `read_graph` unless a full-graph view is required.
