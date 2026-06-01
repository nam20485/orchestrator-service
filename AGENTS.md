## Learned User Preferences

- Wants repo-visible POR/spec documents (e.g. `plan_docs/plan.md`) with requirements, acceptance criteria, validation plan, and phased development—not Cursor-internal plan files alone for approval.
- Does not want RFC-style planning writeups; wants a formal spec the user can see, save, and approve before implementation.
- OpenCode server containers should be generic and workspace-agnostic; do not mount the host repo by default—clients, prompts, or launchers define code access via `--dir` (default `/workspace` in `scripts/prompt.ps1`).
- Client wrapper scripts (`scripts/prompt.ps1`, `scripts/attach.ps1`) should be thin pass-throughs that require a local `opencode` CLI; no auto-install or Docker client fallback in the first implementation.
- When the server is network-reachable (e.g. Tailscale/LAN), publish port `4099` and require `OPENCODE_SERVER_PASSWORD` at Compose startup.
- Does not use a project `.env` for compose; provider credentials are host/CI environment variables synthesized at container start by `scripts/docker-entrypoint.sh`.
- Client scripts should rely on host `OPENCODE_SERVER_PASSWORD` (opencode CLI default); never hardcode server passwords in committed scripts.
- Before commit, scan changed files for secrets (API keys, hardcoded passwords); never commit API keys or provider credentials.

## Learned Workspace Facts

- Docker image uses `debian:bookworm-slim`, runs `opencode serve` on `0.0.0.0:4099`, and bundles Node.js 24.14.0 (MCP), uv, gh CLI, and Python3.
- Install Node in the Dockerfile via the official `.tar.gz` with `tar -xzf`; `.tar.xz` / `tar -xJf` fails on bookworm-slim because `xz-utils` is not installed.
- One-shot client flow: `opencode run --attach <url>`; interactive attach: `opencode attach <url>` (not `attach --prompt`).
- Authoritative OpenCode server planning spec for this repo: `plan_docs/plan.md`.
- OpenCode server config source of truth is repo `image/` (`opencode.json`, `AGENTS.md`, `.opencode/agents/`, `.opencode/commands/`); Dockerfile copies those into `/app` (no full-repo `COPY . .`); `.dockerignore` excludes non-image files.
- Agent sessions run in `/workspace` (named compose volume); `/app` is server config only—keep working tree separate from OpenCode install/config.
- Root repo `AGENTS.md` is Cursor continual-learning memory only; the container uses `image/AGENTS.md` copied to `/app/AGENTS.md` (overwrites any root copy).
- Provider auth: `scripts/docker-entrypoint.sh` writes `/root/.local/share/opencode/auth.json` from host/CI env vars before `opencode serve` starts.
- Supported provider env vars: `ZAI_CODING_API_KEY` (or `ZAI_API_KEY`), `ZHIPUAI_CODING_API_KEY`, `OPENROUTER_API_KEY`, `ALIBABA_API_KEY`. At least one must be set.
- `zai-coding-plan/glm-4.7` needs `ZAI_CODING_API_KEY`; `OPENROUTER_API_KEY` alone does not authenticate that provider.
- MCP `memory-graph` in `image/opencode.json` uses `@modelcontextprotocol/server-memory` with `MEMORY_FILE_PATH=/app/.memory/memory.jsonl`.
- Compose `environment: - VAR` passes host shell env into the container; `${VAR}` adds `.env` interpolation—this project does not use `.env`.
- Host client scripts: `scripts/prompt.ps1`, `scripts/attach.ps1` (PowerShell thin wrappers to local `opencode`).

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
