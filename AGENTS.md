## Learned User Preferences

- Wants repo-visible POR/spec documents (e.g. `plan_docs/plan.md`) with requirements, acceptance criteria, validation plan, and phased development—not Cursor-internal plan files alone for approval.
- Does not want RFC-style planning writeups; wants a formal spec the user can see, save, and approve before implementation.
- GitHub App webhooks: App handles signed delivery and event subscriptions; orchestration `gh`/API stays on `GH_ORCHESTRATION_AGENT_TOKEN` (PAT). The receiver does not mint installation tokens—app installation tokens are optional, not required for the current design.
- OpenCode server containers should be generic and workspace-agnostic; do not mount the host repo by default—clients, prompts, or launchers define code access via `--dir` (default `/workspace` in `scripts/prompt.ps1`).
- Client wrapper scripts (`scripts/prompt.ps1`, `scripts/attach.ps1`) should be thin pass-throughs that require a local `opencode` CLI; no auto-install or Docker client fallback in the first implementation.
- When the server is network-reachable (e.g. Tailscale/LAN), publish port `4099` and require `OPENCODE_SERVER_PASSWORD` at Compose startup.
- Does not use a project `.env` for compose; provider credentials are host/CI environment variables synthesized at container start by `scripts/docker-entrypoint.sh`.
- Client scripts should rely on host `OPENCODE_SERVER_PASSWORD` (opencode CLI default); never hardcode server passwords in committed scripts.
- Before commit, scan changed files for secrets (API keys, hardcoded passwords); never commit API keys or provider credentials.
- Use `.cursor/skills/scan-uncommitted-secrets` for pre-commit secret checks; use `.cursor/skills/safe-commit` for grouped commits after a clean scan.
- For webhook simulator secrets, avoid new config endpoints; read `OS_WEBHOOK_SECRET` from the server process environment and inject it when serving simulator HTML.
- When describing fixes, distinguish uncommitted local edits from committed repo state and from what a rebuilt/running container image actually contains.
- For local GitHub webhook development, tunnel public HTTPS to host port **80** (Caddy), not receiver **8080**; use **ngrok** or **Tailscale Funnel** (`tailscale funnel 80`; stable `*.ts.net` URL, less churn than free ngrok). With Funnel active, use `compose.yaml` only—do not add `compose.https.yaml` (Funnel and Caddy both bind host **443**).

## Learned Workspace Facts

- Docker image uses `debian:trixie-20260518-slim`, runs `opencode serve` on `0.0.0.0:4099`, and bundles Node.js 24.14.0, pwsh 7.6.2 LTS, uv, gh CLI, Python3, ripgrep, jq, and agent utilities (git, make, openssh-client, gnupg, patch, xz-utils, file, procps); Node and pwsh install from linux-x64 `.tar.gz` tarballs (image is amd64-only; PowerShell uses GitHub tarball because Microsoft apt repo fails on trixie SHA1 policy).
- Authoritative architecture docs: `plan_docs/agent-loop-refactor/architecture.md` and `plan_docs/agent-loop-refactor/application_plan.md` (three-tier Beads pipeline). Original OpenCode server POR (`plan_docs/archive/plan.md`), supervisor spec (`plan_docs/archive/orchestration_supervisor.md`), and maestro options (`plan_docs/archive/maestro_architecture_options.md`) are archived and do NOT reflect current architecture.
- OpenCode server config source of truth is repo `image/` (`opencode.json`, `AGENTS.md`, `.opencode/agents/`, `.opencode/commands/`); Dockerfile copies those into `/app` (no full-repo `COPY . .`), then installs the `.opencode/` tree into the global config dir (`/home/app/.config/opencode`) so `opencode serve` auto-loads it. The entrypoint does NOT export `OPENCODE_CONFIG`/`OPENCODE_CONFIG_DIR` — `opencode serve` auto-loads config from `~/.config/opencode`.
- Agent sessions run in `/workspace` (compose bind mount `${WORKSPACE_DIR}:/workspace`; host-side subdir is created by `scripts/prompt.ps1` before attach); `/app` is server config only—keep working tree separate from OpenCode install/config.
- Root repo `AGENTS.md` is Cursor memory plus host-repo validation docs; the container uses `image/AGENTS.md` copied to `/app/AGENTS.md` (overwrites any root copy).
- Provider auth: `scripts/docker-entrypoint.sh` writes `/home/app/.local/share/opencode/auth.json` from host/CI env vars before `opencode serve` starts; supported vars include `ZAI_CODING_API_KEY` (or `ZAI_API_KEY`), `OPENROUTER_API_KEY`, and `MODEL_STUDIO_API_KEY`; Alibaba Model Studio Singapore (`bailian-payg`) defaults to `bailian-payg/qwen3.6-plus` with `bailian-payg/qwen3.6-flash` as `small_model`.
- Non-root execution: all three containers run as non-root (`app` UID 1000 by default via gosu entrypoint; Caddy runs as a `caddy` user created in the image — the pinned `caddy:2.10.0-alpine` ships no non-root user — via a root entrypoint + `su-exec` drop, with `:80`/`:443` granted by a file capability on `/usr/bin/caddy` (`setcap cap_net_bind_service=+ep`) plus compose `cap_add: CAP_NET_BIND_SERVICE`). Workspace files are operator-owned — no `sudo` for cleanup. The `app`/`caddy` users are baked at **build** time (`ARG APP_UID`/`APP_GID` configure `app` only); the compose files set **no** runtime `user:` because it would bypass the root→`gosu`/`su-exec` drop and break ownership on non-1000 hosts. To run as a different UID, **rebuild** with `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)` (via `compose.build.yaml`), not a runtime env var. One-time migration for pre-existing root-owned files: `sudo chown -R $(id -u):$(id -g) $WORKSPACE_DIR`.
- `zai-coding-plan/glm-4.7` needs `ZAI_CODING_API_KEY`; `OPENROUTER_API_KEY` alone does not authenticate that provider.
- MCP `memory-graph` in `image/opencode.json` uses `@modelcontextprotocol/server-memory` with `MEMORY_FILE_PATH=/app/.memory/memory.jsonl`; compose volume `opencode-memory` persists it.
- OpenCode config: use `default_agent` (not `agent`); remote MCPs like `microsoft-learn` need `type: "remote"` and `enabled: true`.
- Compose `environment: - VAR` passes host shell env into the container; `${VAR}` adds `.env` interpolation—this project does not use `.env`.
- Host client scripts: `scripts/prompt.ps1`, `scripts/attach.ps1` (PowerShell thin wrappers to local `opencode`; pwsh is a host prerequisite); one-shot via `opencode run --attach <url>`, interactive via `opencode attach <url>`.
- GitHub webhook stack: `webhook_receiver/` FastAPI validates App webhooks with `OS_WEBHOOK_SECRET` and dispatches OpenCode via `scripts/prompt.ps1` (`-PromptFile` for large payloads); `webhook-receiver` (internal :8080) behind `webhook-proxy` (Caddy on host :80; prod TLS via `compose.https.yaml` for :443); path `/webhooks/github`. Hybrid auth: App for delivery/subscriptions; agent `gh`/API uses `GH_ORCHESTRATION_AGENT_TOKEN` (PAT), not installation JWT. Subscribing to `issues` requires App **Issues: Read** (read is enough for webhook delivery). Dev simulator at `/simulator` when `WEBHOOK_ENABLE_SIMULATOR=1` (off by default), `OS_WEBHOOK_SECRET` injected server-side. Local Funnel: `compose.yaml` only; prod: `COMPOSE_FILE=compose.yaml:compose.https.yaml`.

## Current Architecture

The system is a **three-tier software factory** built around the Beads DAG ecosystem. Authoritative architecture docs: `plan_docs/agent-loop-refactor/architecture.md` and `plan_docs/agent-loop-refactor/application_plan.md`.

### Three-Tier Pipeline

| Phase | Actor | Input | Action | Output |
|-------|-------|-------|--------|--------|
| **1. Ideation** | `/perfect-idea` skill (PM agent) | A loose human idea | Interrogates constraints & architecture via conversation | `application_plan.md` |
| **2. Planning** | `/plan-to-beads` skill (Scrum agent) | `application_plan.md` | Derives Epics/Tasks/ACs and maps DAG dependencies | `.beads/` Graph DAG |
| **3. Execution** | `BeadsLoop` (background thread) | `.beads/` Graph DAG | Spawns isolated agents per bead to write code, test, and close beads | Working software + PRs |

### Service Roles

- **orchestratorservice** — OpenCode server (`opencode serve` on :4099). Hosts agent sessions. Config in `/app` (`opencode.json`, `AGENTS.md`, `.opencode/`). Working directory `/workspace` (host bind mount `${WORKSPACE_DIR}:/workspace`, shared with webhook-receiver). Clients attach via `opencode run --attach <url> --dir <workspace>`.
- **webhook-receiver** — FastAPI app on :8080. Validates GitHub App webhooks, dispatches orchestration runs (fire-and-forget via `scripts/prompt.ps1`). Also hosts the `BeadsLoop` background daemon thread that polls `br ready --json` and spawns agents per bead.
- **webhook-proxy** — Caddy reverse proxy on host :80 (HTTP) or :443 (HTTPS via `compose.https.yaml`).

### Normal States vs. Errors

When classifying behavior as "failure" vs. "as-designed":

- **"Beads not initialized"** (`NOT_INITIALIZED`, `no workspace config`) is a **normal startup state**, NOT an error. The service starts before any work is planned. `BeadsLoop` logs INFO once and waits. When a user triggers `/plan-to-beads`, beads are created and the loop picks them up — no restart required. The service is **"ready at will."**
- **Empty `br ready --json`** (no unblocked beads) is a **normal idle state**. The loop waits for work.
- **`br ready` with non-`NOT_INITIALIZED` errors** (e.g. `db locked`) IS an error — logged at ERROR level.
- **Agent failure to run `br close`** is handled by retry logic (up to `BEADS_MAX_RETRIES`), not a hard crash.

### Data Flow

```
GitHub webhook → webhook-receiver → orchestrator agent (via prompt.ps1)
                                         ↓
                                    User triggers skill:
                                    /perfect-idea → application_plan.md
                                    /plan-to-beads → .beads/ DAG
                                         ↓
                                    BeadsLoop (background thread):
                                    poll br ready --json → spawn agent per bead
                                    → verify br close → push branch → create PR
```

### Coexistence

The Beads pipeline is **additive**. The existing label-driven orchestration (`orchestration_prompt.jinja2.md`, match-case branching) is preserved. Both systems operate concurrently without interference.

## Outdated Documentation

These docs are **historical/archived** and do NOT reflect current architecture. Do not use them for implementation guidance:

- `plan_docs/archive/plan.md` — Original OpenCode Server POR. Uses port 4096 (now 4099). Describes `scripts/opencode/prompt.sh` (now `scripts/prompt.ps1`). Predates the beads integration.
- `plan_docs/archive/orchestration_supervisor.md` — Future maestro/supervisor design. NOT implemented. Describes leapfrog recovery pattern.
- `plan_docs/archive/maestro_architecture_options.md` — Future architecture options for the maestro. NOT implemented.
- `docs/agent-loop-dev-plans/` — Original refactor plans with inaccuracies. Corrected by `plan_docs/agent-loop-refactor/architecture.md` ("Corrections from Original Plans" section).

## Validation

All changes must be validated — as they are implemented and before committing. The three mandatory steps are **build, scan, test**; locally this repo runs **lint, scan, test** (build is CI-only).

| Check | Command | CI job (`validate.yml`) |
|-------|---------|-------------------------|
| All (local) | `./scripts/validate.ps1 -All` | lint + scan + test (not build) |
| Lint | `./scripts/validate.ps1 -Lint` | `lint` |
| Secret scan | `./scripts/validate.ps1 -Scan` | `scan` |
| Tests | `./scripts/validate.ps1 -Test` | `test` |
| Docker image build | *(CI only)* | `build` |

After any non-trivial change (code, config, workflows, Docker):

1. Run `pwsh -NoProfile -File ./scripts/validate.ps1 -All`.
2. Fix all failures; re-run until clean.
3. Only then commit and push.

The validation script must mirror exactly what CI runs — keep `scripts/validate.ps1` and `.github/workflows/validate.yml` in sync. Missing local tools: `pwsh -NoProfile -File ./scripts/install-dev-tools.ps1`.

### Missing Validation Script

If the expected validation script does not exist:

1. **Create it** at the repo root with the platform-appropriate extension (`.ps1` for Windows, `.sh` for Unix).
2. **Implement build → scan → test** in order; each step must fail fast (non-zero exit) on error.
3. **Make it executable** (`chmod +x` on Unix; on Windows ensure execution policy allows it).
4. **Commit it** as its own change before running, so CI picks it up on the same branch.
5. **Mirror CI/CD** — match any existing pipeline config (`.github/workflows/`, `azure-pipelines.yml`); if none exists, choose sensible defaults and document them in a comment at the top.

## Testing

An automated test suite must be maintained, with results and coverage reports generated automatically. **Test coverage must stay > 85%** as new code is added.

- Full suite: `pwsh -NoProfile -File ./scripts/validate.ps1 -Test` (or `-All` for lint + scan + test).
- Python: `uv sync --group dev` then `uv run pytest tests/ -q`.
- Pester: `pwsh -NoProfile -File ./test/run-pester-tests.ps1`.
- Bash: `test/test-docker-entrypoint.sh`, `test/test-compose-config.sh`, `test/test-caddyfile.sh`, `test/test-opencode-json.sh`.
- Webhook fixtures: `test/fixtures/github/` (use `FAKE-KEY-FOR-TESTING-…` only; never `ghp_`, `sk-`, `AKIA`, etc. in fixtures).

### Test Driven Development (TDD)

When implementing new features, use TDD:

- Implement failing tests to cover the required functionality.
- Implement changes to make the tests pass.
- Iterate creating tests and implementing changes until the functionality is complete.

## Committing

### Pre-commit checklist

- Ran `./scripts/validate.ps1 -All` (or the relevant `-Lint` / `-Scan` / `-Test` subset).
- Secret scan clean (`.cursor/skills/scan-uncommitted-secrets` or `validate.ps1 -Scan`).
- No real API keys or tokens in committed files.
- Always run the `/safe-commit` skill before committing.

### Branching

- Create a new branch for each feature or bug fix.
- Use a descriptive name in the form `<base-branch-prefix>/<branch-name>`, i.e. `mn/new-feature` or `dev/<branch-name>`.

### Pull Requests

- Create a pull request for each branch with a descriptive title and description.
- Request a review from the appropriate team member before merging.
- Address **all** review comments before merging; for each, leave a reply explaining the resolution and mark the thread RESOLVED.

## Delegation

- Delegate work to the appropriate subagent type when possible.
- If you are the top-level agent — especially if your type is not relevant to the current task — prefer delegating.
- Delegate to parallel agents to speed up work and reduce implementation time.

## Orchestration

Use orchestration agents to **decompose and delegate** work instead of implementing it all yourself. Pick the **smallest layer** that fits the scope — do not spawn a higher layer for work a lower one (or you directly) can handle.

- `orchestrator` — top-level coordinator for multi-step, multi-agent tasks. Breaks the work into a dependency graph and dispatches units to specialists (`planner`, `developer`, `code-reviewer`, `qa-tester`, `researcher`) in parallel batches. Use as the default for non-trivial, multi-part work.
- `team-lead` — owns a **single workstream** (one feature/epic/fix) end-to-end: reviews the plan, assigns specialists, and enforces the definition of done. Use when the work fits within one accountable owner.
- `team-orchestrator` — runs a **program of multiple parallel workstreams** by delegating each to a `team-lead` and managing cross-team dependencies. Use only for efforts too large for one `team-lead`; otherwise delegate straight to a `team-lead`.

## Making Changes

- Always make the smallest, most surgical change possible.
- Only make changes that are necessary to fix the issue at hand.
- Ignore areas not relevant to the current task.

## Investigation

- Never guess at the cause of an issue.
- Always investigate using first-hand sources: logs, code, output.
- Do not make or report assertions without specific details (line numbers, files, log messages) to back them up.
- Do not start implementing a solution until you have decisively found the root cause.

## Planning

- Always create a plan before starting any non-trivial task (e.g. >= 3 steps or >= 5 minutes of work).
- Present plans for approval before starting any non-trivial task.
- Always use TODO lists to track work; mark items complete as done.
- Present a summary after completing all plans/tasks.

## Scripts (`scripts/`)

PowerShell thin wrappers and helpers. Dot-source auth helpers; run others directly.

| Script | Purpose | When / how |
|--------|---------|------------|
| `validate.ps1` | Local validation (lint, scan, test) mirroring CI. | After any non-trivial change: `pwsh -NoProfile -File ./scripts/validate.ps1 -All` (or `-Lint`/`-Scan`/`-Test`). |
| `install-dev-tools.ps1` | Installs uv dev deps, Pester, actionlint, shellcheck, jq, docker hints, and Beads (`br`, `bvr` via cargo+nightly). | Missing local tools: run once per machine before validating. |
| `common-auth.ps1` | `Initialize-GitHubAuth` (interactive `gh auth login` fallback). | Dot-source from other scripts that need gh auth. |
| `gh-auth.ps1` | `Initialize-GitHubAuth` with PAT support (`-Token` / `$GITHUB_AUTH_TOKEN`, non-interactive). | Dot-source when running unattended (CI, agent) with a PAT. |
| `prompt.ps1` | Dispatch a one-shot OpenCode run via `opencode run --attach <url>`. Core orchestration launcher. | `pwsh -File ./scripts/prompt.ps1 -PromptFile <path>` (large payloads) or `-Prompt "<text>"`. |
| `attach.ps1` | Interactive OpenCode attach (`opencode attach <url>`). | `pwsh -File ./scripts/attach.ps1` for interactive sessions on a running server. |
| `docker-entrypoint.sh` | Container entrypoint: writes `auth.json` from env vars, then `exec`s the server. | Runs as container entrypoint; not invoked manually. Sets `ZAI_CODING_API_KEY`/`OPENROUTER_API_KEY`/`MODEL_STUDIO_API_KEY`. |
| `query.ps1` | List/resolve unresolved PR review threads via GraphQL; optional reply-then-resolve. | `pwsh -File ./scripts/query.ps1 -Owner <o> -Repo <r> -PullRequestNumber <n>`; add `-DryRun`, `-AutoResolve`, `-ReplyEach "<msg>"`. |
| `import-labels.ps1` | Sync labels from a JSON export (`gh api .../labels`) into a repo (create/update/`-DeleteMissing`). | `pwsh -File ./scripts/import-labels.ps1 -Repo owner/repo -LabelsFile ./.labels.json [-DeleteMissing]`. |
| `create-milestones.ps1` | Create milestones from `-Titles` or `-TitlesFile`. | `pwsh -File ./scripts/create-milestones.ps1 -Repo owner/repo -Titles "Phase 1","Phase 2" [-DryRun] [-SkipExisting]`. |
| `test-github-permissions.ps1` | Verify gh auth + scopes (repo, project) and repo/milestone/branch/PR operations. | `pwsh -File ./scripts/test-github-permissions.ps1 -Owner <user>`; add `-AutoFixAuth` to refresh scopes. |

Notes: `prompt.ps1`/`attach.ps1` resolve server URL as: `-ServerUrl` > `$OPENCODE_SERVER_URL` > `$OPENCODE_HOST`/`$OPENCODE_PORT` > `http://localhost:4099`. They rely on host `OPENCODE_SERVER_PASSWORD` (never hardcoded). Default model in `prompt.ps1` is `zai-coding-plan/glm-4.7`.

## After Push

Monitor CI until green: `gh run list --limit 5`, `gh run watch <id>`, `gh run view <id> --log-failed`. Required workflow: **validate** (lint, scan, test, build). Do not mark work complete while CI is red. If a workflow fails, investigate and fix before proceeding; repeat until all workflows pass.

### Diagnosing GHA failures (mandatory — no guessing)

**Never guess at GitHub Actions workflow failures.** Before proposing any fix:

1. Fetch latest run outcomes: `gh run list --workflow=<file> --limit 5` (e.g. `--workflow=validate.yml`).
2. Identify the failing job from the run summary.
3. Fetch the actual logs: `gh run view <id> --log-failed` (failing steps only) or `gh run view <id> --log` (full).
4. Read the exact error lines.

Only diagnose root cause from **verifiable log output**. Do not theorize about a fix (or about *why* a valid ref/SHA was rejected) before reading the real failure log.

When a root cause is determined and communicated, **display the log line(s) that verify the diagnosis** — quote the verbatim error line(s) from the run log alongside the explanation. Never state a root cause without showing the supporting log evidence.

## Agent Instructions

- Bootstrap entry point for the agent instruction set: core module locations, remote loading protocol, and single-source-of-truth policy.
- Start with Core Instructions, then follow links to other modules as the request requires.

## Configuration

- **Branch:** `optimization` — change to load instructions from another branch (`optimization`, `feature/*`, or any valid branch name).
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

Always use sequential-thinking and the Memory knowledge-graph for all non-trivial tasks.

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
- For **durable, reusable context only** — never transient scratch state or secrets/PII (the store is plaintext). Search before creating to avoid duplicates; keep observations atomic, specific, and active-voiced.

### Web & Repository Research (Z.AI MCP)

Three **remote** Z.AI MCP servers authenticate via the `Authorization: {env:Z_AI_API_KEY}` header and require no local install. Use them for reliable, structured external information retrieval instead of ad-hoc fetching.

- **`web-search-prime`** → `webSearchPrime` — Web search returning titles, URLs, summaries, site names, and icons. Use for best-practice surveys, competitive analysis, dependency/API research, and factual questions needing current external info. Key params: `content_size` (`medium` default, `high` for comprehensive), `location` (`cn` / `us`), `search_domain_filter` (whitelist a domain), `search_recency_filter` (`oneDay` / `oneWeek` / `oneMonth` / `oneYear` / `noLimit`). Keep queries ≤ 70 chars.
- **`web-reader`** → `webReader` — Fetches a URL and converts it to large-model-friendly input (markdown/text/html). Returns page title, main content, metadata, and optional link/image summaries. Use to read API docs, articles, release notes, and reference pages. Prefer this over generic `webfetch` when available.
- **`zread`** — Reads **public** GitHub repositories without cloning: `search_doc` (search docs/issues/commits/PRs/contributors), `get_repo_structure` (directory tree + file list), and `read_file` (full file contents). Use for dependency evaluation, "how does library X work?" questions, and issue/commit history lookups. Requires `owner/repo` names; only public repos are supported.

Decision points:

- Need current facts from the open web → `webSearchPrime`, then `webReader` to drill into a specific result.
- Need to understand an open-source repo → `zread` first (`get_repo_structure` + `search_doc`), then `read_file` for implementation details.
- For broad, multi-source surveys, delegate to the `researcher` subagent; use these tools directly for quick, single-shot lookups.

### Exa Search (MCP)

The **remote** Exa MCP server authenticates via `exaApiKey={env:EXA_API_KEY}` and requires no local install. Use it as a complement to Z.AI when its neural search, code-context, or crawling fits better.

- **`web_search_exa`** — Keyword/neural web search. Fallback when Z.AI `webSearchPrime` is rate-limited.
- **`web_search_advanced_exa`** — Filtered search (date, domain, text-match, count). Scoped queries.
- **`web_fetch_exa`** — Fetch a URL to clean markdown/text. Alternative to Z.AI `webReader`.
- **`get_code_context_exa`** — Code context (functions, types, usage) for "how is X used?" before `zread` for full files.
- **`crawling_exa`** — Crawl multiple pages of a site; collect a doc subsite in one call.

Prefer Z.AI `webSearchPrime`/`webReader` as the default for single-shot lookups; reach for Exa when its neural search, code-context, or crawling fits better.
