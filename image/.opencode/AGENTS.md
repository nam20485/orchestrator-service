---
file: AGENTS.md
description: Project instructions for coding agents
scope: repository
---

<instructions>
  <purpose>
    <summary>
      Dockerized opencode orchestration RUNTIME. A docker-compose stack runs `opencode serve` (:4099),
      a Python `webhook_receiver` (FastAPI, :8080), and a Caddy reverse proxy (:80). GitHub webhooks
      from downstream app-instance repos deliver `issues:labeled` events; the receiver matches the
      label, renders a prompt, and dispatches a NON-INTERACTIVE `opencode run --attach` against the
      instance workspace. The orchestrator agent analyzes the prompt against match-clauses and
      delegates work to specialist subagents in `.opencode/agents/`. This repo builds its OWN GHCR
      images from `Dockerfile` + `image/` (no external prebuild/devcontainer, no GitHub-Actions dispatch).
    </summary>
  </purpose>

  <multi_repo_system>
    <summary>
      This repo is one node in a software factory. Cross-system coupling is via GitHub ISSUES AS A
      MESSAGE BUS (no in-repo GitHub Actions drive dispatch).
    </summary>
    <repos>
      <repo>**Template** `intel-agency/agent-context` — the GitHub template each app instance is cloned from. Carries its own `.opencode/agents` + `.agents` (memory/rules/`gh-issue-tracking-init` skill) for LOCAL interactive use; contains NO workflows/devcontainer/webhook code/`plan_docs`.</repo>
      <repo>**Factory** `nam20485/workflow-launch2` — entry `scripts/create-repo-agent-context.ps1` clones the template, seeds `plan_docs/<slug>/`, Class-2 cleanup, imports labels (`.github/.labels.json`), and files the dispatch issue.</repo>
      <repo>**Runtime** `orchestrator-service` (THIS repo) — docker-compose opencode webhook runtime that receives events and dispatches non-interactive prompts.</repo>
      <repo>**Instances** `intel-agency/<app>-<suffix>` (e.g. `gap-miner-v2-tango85`) — cloned+seeded repos the runtime implements against.</repo>
      <repo>**Workflow store** `nam20485/agent-instructions` — canonical dynamic-workflow definitions fetched FRESH by the orchestrator at dispatch time.</repo>
    </repos>
    <dispatch_contract>
      The factory files a dispatch issue on the new instance repo: **label `gh-issue-tracking:direct-body`**,
      **body `/gh-issue-tracking-init`** (gated by fail-closed `DIRECT_BODY_ALLOWED_SENDERS`). A GitHub
      repo webhook delivers the `issues:labeled` event → `webhook_receiver` HMAC-verifies
      (`OS_WEBHOOK_SECRET`) → matches the label (`webhook_receiver/filters.py` `should_dispatch`) →
      renders the prompt (`webhook_receiver/prompts.py` from `orchestration_prompt.jinja2.md`) →
      `webhook_receiver/runner.py` `dispatch_to_opencode()` → `scripts/prompt.ps1` →
      `opencode run --attach http://orchestratorservice:4099 --dir /workspace/<slug> --agent orchestrator --auto`.
      *(Legacy, superseded trigger: `orchestration:dispatch` label + `/orchestrate-dynamic-workflow`.)*
      KNOWN GAP: nothing currently drives implementation after `/gh-issue-tracking-init` builds the Plan/Epic/Story hierarchy.
    </dispatch_contract>
  </multi_repo_system>

  <tech_stack>
    <item>opencode CLI (v1.18.4) — agent runtime: `opencode serve` on :4099; dispatched as `opencode run --model qwencloud/qwen3.8-max --variant high --agent orchestrator`.</item>
    <item>Z.AI GLM models (`glm-5.3-flash` for subagents and small model) via `ZAI_CODING_API_KEY`; orchestrator default `qwencloud/qwen3.8-max` via `QWENCLOUD_TOKEN_PLAN_API_KEY`; `OPENROUTER_API_KEY`, `MODEL_STUDIO_API_KEY` for alternates.</item>
    <item>Python (FastAPI) `webhook_receiver/` — webhook validation (HMAC), label matching, prompt rendering, dispatch.</item>
    <item>docker-compose stack — `orchestratorservice` (opencode serve :4099) + `webhook-receiver` (FastAPI :8080) + `webhook-proxy` (Caddy :80). Self-built GHCR images from this repo's `Dockerfile`/`Dockerfile.webhook` + `image/` (CI: `.github/workflows/docker-publish.yml`).</item>
    <item>PowerShell (`pwsh`) host/client scripts — `scripts/prompt.ps1` is the non-interactive dispatch wrapper.</item>
    <item>MCP servers (enabled): `@modelcontextprotocol/server-sequential-thinking`, `@modelcontextprotocol/server-memory` (knowledge-graph at `/app/.memory/memory.jsonl`; single-writer protocol — see `mandatory_tool_protocols.persistent_memory`), remote Z.AI `web-reader`/`zread`/`web-search-prime`.</item>
  </tech_stack>

  <repository_map>
    <!-- Runtime stack -->
    <entry><path>compose.yaml</path><description>3-service docker-compose stack: `orchestratorservice` (opencode serve :4099), `webhook-receiver` (FastAPI :8080 internal), `webhook-proxy` (Caddy :80). Pulls self-built GHCR images; `compose.build.yaml` rebuilds locally.</description></entry>
    <entry><path>Dockerfile</path><description>Multi-stage image build (rust-builder for `br` + debian:trixie-slim final). `COPY image/ /app/`, installs `image/.opencode/` → `/home/app/.config/opencode/` (global config), writes `auth.json` from provider env, drops to non-root `app` via gosu.</description></entry>
    <entry><path>Dockerfile.webhook</path><description>webhook-receiver Python image (uv-managed, `webhook_receiver/` FastAPI app).</description></entry>
    <!-- Webhook receiver (the dispatch engine) -->
    <entry><path>webhook_receiver/app.py</path><description>HTTP entry: `POST /webhooks/github` — signature verify, `should_dispatch` gate, `build_orchestrator_prompt`, background `dispatch_to_opencode`.</description></entry>
    <entry><path>webhook_receiver/filters.py</path><description>`should_dispatch` + log blacklist — label-prefix matching (`gh-issue-tracking:`/`orchestration:`), `direct-body` sender allowlist.</description></entry>
    <entry><path>webhook_receiver/prompts.py</path><description>Renders the Jinja2 `orchestration_prompt.jinja2.md` match-clause state machine.</description></entry>
    <entry><path>webhook_receiver/runner.py</path><description>`dispatch_to_opencode()` + `IdleWatchdog` run classifier (completed/failed/idle_timeout/zero-work).</description></entry>
    <entry><path>scripts/prompt.ps1</path><description>Non-interactive dispatch: `opencode run --attach <url> --dir <workspace> --model … --agent orchestrator --auto`.</description></entry>
    <!-- Agent/config source -->
    <entry><path>image/.opencode/</path><description>opencode config shipped into the container: `opencode.json`, THIS `AGENTS.md`, `agents/` (orchestrator + 8 specialists: code-reviewer, developer, documentation-expert, github-expert, odbplusplus-expert, planner, qa-test-engineer, researcher), `commands/`, `local_ai_instruction_modules/`.</description></entry>
    <entry><path>image/.opencode/opencode.json</path><description>`instructions:["AGENTS.md"]`, `default_agent:"orchestrator"`, `model:qwencloud/qwen3.8-max` (orchestrator), subagents pinned to `zai-coding-plan/glm-5.3-flash`, per-agent model+variant overrides, MCP defs, `"permission": "allow"` (server-side: allows all actions for all sessions including subagents — the definitive fix for the headless permission deadlock).</description></entry>
    <!-- CI -->
    <entry><path>.github/workflows/</path><description>`validate` (lint/scan/test), `docker-publish` (build+push GHCR images), `trivy` (image scan), `opencode`, `dependency-review`, `droid`/`droid-review`. There is NO `orchestrator-agent.yml` workflow in this repo.</description></entry>
    <!-- Docs -->
    <entry><path>docs/</path><description>Operational docs: `deployment-compose.md`, `dashboard.md`, `testing-approach.md`, `tool-memory.md`, `orchestrator-run-logs.md`, `plan-server-activity-watchdog.md`, incident postmortems.</description></entry>

    <opencode_server>
      <summary>
        `opencode serve` listens on :4099 (in-container). The webhook-receiver dispatches via
        `opencode run --attach http://orchestratorservice:4099 --dir /workspace/<slug>`. Server password
        is `OPENCODE_SERVER_PASSWORD` (fail-closed). Server config auto-loads from `/home/app/.config/opencode/`.
      </summary>
    </opencode_server>
  </repository_map>

  <instruction_source>
    <repository>
      <name>nam20485/agent-instructions</name>
      <branch>main</branch>
    </repository>
    <guidance>
      Remote instructions are the single source of truth. Fetch from raw URLs:
      replace `github.com/` with `raw.githubusercontent.com/` and remove `blob/`.
      Core instructions: `https://raw.githubusercontent.com/nam20485/agent-instructions/main/ai_instruction_modules/ai-core-instructions.md`
    </guidance>
    <modules>
      <module type="core" required="true" link="https://github.com/nam20485/agent-instructions/blob/main/ai_instruction_modules/ai-core-instructions.md">Core Instructions</module>
      <module type="local" required="true" path="local_ai_instruction_modules">Local AI Instructions</module>
      <module type="local" required="true" path="local_ai_instruction_modules/ai-dynamic-workflows.md">Dynamic Workflow Orchestration</module>
      <module type="local" required="true" path="local_ai_instruction_modules/ai-workflow-assignments.md">Workflow Assignments</module>
      <module type="local" required="true" path="local_ai_instruction_modules/ai-development-instructions.md">Development Instructions</module>
      <module type="optional" path="local_ai_instruction_modules/ai-terminal-commands.md">Terminal Commands</module>
    </modules>
  </instruction_source>

  <environment_setup>
    <secrets>
      <item>`ZAI_CODING_API_KEY` (or `ZAI_API_KEY`) — Z.AI GLM model access (primary).</item>
      <item>`OPENROUTER_API_KEY`, `MODEL_STUDIO_API_KEY` — alternate providers.</item>
      <item>`OS_WEBHOOK_SECRET` — GitHub webhook HMAC secret (required, fail-closed).</item>
      <item>`OPENCODE_SERVER_PASSWORD` — `opencode serve` auth (required, fail-closed).</item>
      <item>`WORKSPACE_DIR` — host path bind-mounted to `/workspace` (required).</item>
      <item>`DIRECT_BODY_ALLOWED_SENDERS` — fail-closed allowlist gating `gh-issue-tracking:direct-body` dispatch.</item>
      <item>`GH_ORCHESTRATION_AGENT_TOKEN` — org-level PAT (repo, workflow, project, read:org) for agent `gh`/API calls. No fallback to `GITHUB_TOKEN`.</item>
    </secrets>
    <image_build>
      This repo builds its OWN GHCR images from `Dockerfile` (orchestratorservice) + `Dockerfile.webhook`
      (webhook-receiver) + `image/`, published by `.github/workflows/docker-publish.yml`. `compose.yaml`
      pulls `ghcr.io/nam20485/orchestrator-service:*-latest`. There is NO external prebuild/devcontainer repo.
    </image_build>
  </environment_setup>

  <testing>
    <guidance>Host repo tests live under `tests/` (pytest), `test/*.sh` (bash), and `test/*.Tests.ps1` (Pester). Invoke via `./scripts/validate.ps1 -Test` or `-All` on the orchestrator-service clone.</guidance>
    <commands>
      <command>All validation (preferred): `pwsh -NoProfile -File ./scripts/validate.ps1 -All`</command>
      <command>Tests only: `pwsh -NoProfile -File ./scripts/validate.ps1 -Test`</command>
      <command>Python: `uv run pytest tests/ -q`</command>
      <command>Pester: `pwsh -NoProfile -File ./test/run-pester-tests.ps1`</command>
      <command>Docker entrypoint: `bash test/test-docker-entrypoint.sh`</command>
      <command>Compose config: `bash test/test-compose-config.sh`</command>
      <command>Caddyfile: `bash test/test-caddyfile.sh`</command>
      <command>opencode.json: `bash test/test-opencode-json.sh`</command>
    </commands>
    <guidance>Add webhook fixture payloads to `test/fixtures/github/` when testing new event types. Use synthetic secrets only (`FAKE-KEY-FOR-TESTING-…`).</guidance>
  </testing>

  <coding_conventions>
    <rule>Keep changes minimal and targeted.</rule>
    <rule>Do not hardcode secrets/tokens. When writing tests for credential-scrubbing or secret-detection utilities, use obviously synthetic values that will not trigger `gitleaks` (e.g., `FAKE-KEY-FOR-TESTING-00000000`). Never use prefixes that match real provider formats (`sk-`, `ghp_`, `ghs_`, `AKIA`, etc.) in test fixtures.</rule>
    <rule>Preserve the `__EVENT_DATA__` placeholder in `orchestrator-agent-prompt.md`.</rule>
    <rule>Keep orchestrator delegation-depth ≤2 and "never write code directly" constraint.</rule>
    <rule>Pin ALL GitHub Actions by full SHA to the latest release — no tag or branch references (`@v4`, `@main`). Format: `uses: owner/action@<full-40-char-SHA> # vX.Y.Z`. The trailing comment with the semver tag is mandatory for human readability. This applies to every `uses:` line in every workflow file, including third-party actions, first-party (`actions/*`), and reusable workflows. Supply-chain attacks via tag mutation are a critical threat — SHA pinning is the only mitigation. When creating or modifying workflows, look up the SHA for the latest release of each action (e.g., via `gh api repos/actions/checkout/releases/latest --jq .tag_name` then resolve to SHA) and pin to it.</rule>
    <rule>Never add duplicate top-level `name:`, `on:`, or `jobs:` keys in workflow YAML.</rule>
      <rule>`image/.opencode/` is the agent config shipped into the container — the `Dockerfile` DOES `COPY image/ /app/` then installs it to `/home/app/.config/opencode/`. There is NO external prebuild repo; images build in this repo.</rule>
      <rule>Server-side permission config (`"permission": "allow"` in `image/.opencode/opencode.json`) governs ALL sessions including task-spawned subagents. The client-side `--auto` flag is belt-and-suspenders. Agent frontmatter `external_directory` rules are defense-in-depth only.</rule>
    <rule>Repository labels are defined in `.github/.labels.json`. Use `scripts/import-labels.ps1` to sync them to a repo instance. When adding new labels, add them to this file — it is the single source of truth for the label set.</rule>
    <rule>Implementation approval protocol: before implementing any non-trivial change, verify that explicit approval was given for that specific item AND that no significant state or circumstances have changed since approval was given. If approval was never given, or was invalidated by changed circumstances, stop and ask before acting. When in doubt — ask, don't act.</rule>
  </coding_conventions>

  <!-- ═══════════════════════════════════════════════════════════════════
       MANDATORY TOOL PROTOCOLS — ALL AGENTS MUST FOLLOW
       These are NON-NEGOTIABLE requirements for every agent in this system.
       Failure to follow these protocols is a critical defect.
       ═══════════════════════════════════════════════════════════════════ -->
  <mandatory_tool_protocols>
    <overview>
      ALL agents — orchestrator, specialists, and subagents — MUST use the following
      MCP tools as part of their standard operating procedure. These are not optional
      suggestions; they are mandatory requirements that apply to every non-trivial task.
      Agents that skip these protocols are operating incorrectly.
    </overview>

    <protocol id="sequential_thinking" enforcement="MANDATORY">
      <title>Sequential Thinking Tool — ALWAYS USE</title>
      <tool>sequential_thinking</tool>
      <when>
        EVERY non-trivial task. This means any task that involves more than a single
        obvious action. If in doubt, use it.
      </when>
      <required_usage_points>
        <point>At task START: Use sequential thinking to analyze the request, break it into steps, identify risks, and plan the approach BEFORE taking any action.</point>
        <point>At DECISION POINTS: Use sequential thinking when choosing between alternatives, evaluating trade-offs, or making architectural decisions.</point>
        <point>When DEBUGGING: Use sequential thinking to systematically isolate root causes.</point>
        <point>Before DELEGATION: The Orchestrator MUST use sequential thinking to plan the delegation tree, determine agent assignments, and define success criteria.</point>
      </required_usage_points>
      <violation>Skipping sequential thinking on a non-trivial task is a protocol violation. If an agent completes a complex task without invoking sequential_thinking, the work should be reviewed for quality issues.</violation>
    </protocol>

    <protocol id="persistent_memory" enforcement="MANDATORY">
      <title>Persistent Memory — ALWAYS USE (single-writer: orchestrator only)</title>
      <summary>
        The memory-graph store (`@modelcontextprotocol/server-memory`) writes the entire
        `memory.jsonl` via an unprotected `writeFile` on every mutation. Concurrent writers
        (the orchestrator session AND each subagent session each spawn their own server-memory
        process against the one `MEMORY_FILE_PATH`) interleave their writes and corrupt the file.
        To eliminate the corruption at its source, this system is **single-writer**: the
        Orchestrator is the ONLY agent that calls memory WRITE tools. All other agents are
        READ-ONLY and return facts to persist via the Memory Save Requests hand-off (below).
        Reads never corrupt the file, so read-many is safe; write-one is enforced.
      </summary>
      <read_tools available_to="ALL AGENTS">
        <tool>search_nodes</tool>
        <tool>open_nodes</tool>
        <tool>read_graph</tool>
      </read_tools>
      <write_tools available_to="ORCHESTRATOR ONLY">
        <rule>Subagents and specialists MUST NOT call these tools. They are write operations
        that can corrupt memory.jsonl when run concurrently. Subagents return facts via the
        Memory Save Requests hand-off; the Orchestrator persists them.</rule>
        <tool>create_entities</tool>
        <tool>create_relations</tool>
        <tool>add_observations</tool>
        <tool>delete_entities</tool>
        <tool>delete_observations</tool>
        <tool>delete_relations</tool>
      </write_tools>
      <required_usage_points>
        <point>At task START (ALL agents): Call `search_nodes` and/or `open_nodes` to retrieve existing context about the project, user preferences, prior decisions, and known patterns BEFORE planning or acting. Use `read_graph` only when a full-graph view is required.</point>
        <point>After SIGNIFICANT WORK (ORCHESTRATOR ONLY): Use `add_observations` on existing entities, or `create_entities` plus `create_relations` for new recurring subjects — including any facts collected from subagents' `## Memory Save Requests` lists.</point>
        <point>After COMPLETING a task (ORCHESTRATOR ONLY): Record outcomes, lessons learned, and follow-up items as atomic observations in the knowledge graph.</point>
        <point>When STARTING a new workflow or assignment (ALL agents): Search for prior related work, decisions, and context with `search_nodes` using repo, issue, workflow, or run keywords.</point>
      </required_usage_points>
      <what_to_store>
        <item>Entities for recurring organizations, repos, issues, workflow runs, or significant events</item>
        <item>Relations between related entities (active voice)</item>
        <item>Atomic observations: one fact per observation string</item>
        <item>User preferences and decisions that affect future tasks</item>
        <item>Architectural decisions and their rationale</item>
        <item>Error patterns and their resolutions</item>
        <item>Cross-task context that would otherwise be lost between sessions</item>
      </what_to_store>
      <memory_save_requests_handoff>
        Subagents (every agent except the Orchestrator) cannot write memory. Instead, at the END
        of their result they MUST include a `## Memory Save Requests` section listing any durable
        facts worth persisting. The Orchestrator reads each subagent's hand-off and persists those
        facts itself using the write tools.
        Format example for a subagent result:
          ## Memory Save Requests
          - Entity: project-foo | Type: microservice | Observation: "uses PostgreSQL 16"
          - Add observation to issue-42: "root cause was missing index on users.email"
        If the subagent has nothing to persist, it omits the section or writes "## Memory Save Requests\n(none)".
      </memory_save_requests_handoff>
      <violation>A subagent calling a memory WRITE tool is a CRITICAL protocol violation (corrupts the store). Failing to read existing memory at task start is a violation. The Orchestrator failing to persist subagent hand-off facts after significant work is a violation.</violation>
    </protocol>

    <protocol id="change_validation" enforcement="MANDATORY">
      <title>Change Validation Protocol — ALWAYS FOLLOW</title>
      <when>
        After ANY non-trivial change to code, configuration, workflows, or infrastructure.
        This includes: logic changes, behavior changes, refactors, dependency updates,
        config changes, multi-file edits, workflow modifications.
      </when>
      <required_steps>
        <step order="1">Run the full validation suite: `pwsh -NoProfile -File ./scripts/validate.ps1 -All`</step>
        <step order="2">Fix ALL failures — do not skip, suppress, or ignore errors.</step>
        <step order="3">Re-run validation until ALL checks pass clean.</step>
        <step order="4">Only THEN proceed to commit and push.</step>
      </required_steps>
      <validation_commands>
        <command purpose="all checks">./scripts/validate.ps1 -All</command>
        <command purpose="lint only">./scripts/validate.ps1 -Lint</command>
        <command purpose="scan only">./scripts/validate.ps1 -Scan</command>
        <command purpose="test only">./scripts/validate.ps1 -Test</command>
        <command purpose="dockerfile">bash test/test-docker-entrypoint.sh</command>
      </validation_commands>
      <post_push>
        After push, monitor CI: `gh run list --limit 5`, `gh run watch &lt;id&gt;`, `gh run view &lt;id&gt; --log-failed`.
        If CI fails, STOP feature work, triage, fix, re-verify, push. Do NOT mark work complete while CI is red.
      </post_push>
      <violation>Committing or pushing code without running validation is a protocol violation. Marking a task complete while CI is failing is a protocol violation.</violation>
    </protocol>

    <agent_checklist>
      <!-- Agents: verify you have completed these items on every non-trivial task -->
      <item>☐ Called sequential_thinking at task start to plan approach</item>
      <item>☐ Called search_nodes / open_nodes (or read_graph) to retrieve prior context</item>
      <item>☐ Used sequential_thinking at key decision points during work</item>
      <item>☐ Ran validation (./scripts/validate.ps1 -All) before commit/push</item>
      <item>☐ Fixed all validation failures and re-verified clean</item>
      <item>☐ Memory: Orchestrator persisted findings (incl. subagent save requests); Subagents returned `## Memory Save Requests` instead of writing</item>
      <item>☐ Subagents: did NOT call any memory WRITE tool (single-writer rule)</item>
      <item>☐ Monitored CI after push and confirmed green</item>
    </agent_checklist>
  </mandatory_tool_protocols>

  <agent_specific_guardrails>
    <rule>The Orchestrator agent delegates to specialists via the `task` tool — never writes code directly.</rule>
    <rule>The Orchestrator is the SOLE memory-graph writer. It MUST invoke `sequential_thinking` before planning any delegation and `search_nodes` (or `open_nodes`) before every new task to load prior project context. After each subagent completes, the Orchestrator reads the subagent's `## Memory Save Requests` list and persists those facts itself using `add_observations` / `create_entities` / `create_relations`. The Orchestrator never asks a subagent to write memory.</rule>
    <rule>Subagents and specialists are memory READ-ONLY: they may call `search_nodes`, `open_nodes`, and `read_graph`, but MUST NOT call `create_entities`, `create_relations`, `add_observations`, or any `delete_*` tool. Concurrent writers corrupt the memory store. Instead, each subagent ends its result with a `## Memory Save Requests` list of facts for the Orchestrator to persist.</rule>
    <rule>ALL agents MUST follow the mandatory_tool_protocols defined above — sequential thinking, memory (single-writer), and change validation are not optional.</rule>
    <rule>Prompt assembly pipeline (runtime, not GitHub Actions):
      1. `webhook_receiver` receives a GitHub `issues:labeled` webhook and HMAC-verifies it (`OS_WEBHOOK_SECRET`).
      2. `should_dispatch` (`webhook_receiver/filters.py`) matches the label (e.g. `gh-issue-tracking:direct-body`).
      3. `build_orchestrator_prompt` (`webhook_receiver/prompts.py`) renders `orchestration_prompt.jinja2.md`, injecting the event JSON.
      4. `dispatch_to_opencode` (`webhook_receiver/runner.py`) spawns `scripts/prompt.ps1`, which runs `opencode run --attach http://orchestratorservice:4099 --dir /workspace/<slug> --agent orchestrator --auto`.
    </rule>
  </agent_specific_guardrails>

  <agent_readiness>
    <verification_protocol>
      MANDATORY: For any non-trivial change (logic, behavior, refactors, dependency updates, config changes, multi-file edits):
      run `./scripts/validate.ps1 -All`, fix all failures, re-run until clean. Do not skip or suppress errors.
      Do NOT commit or push until validation passes. Do NOT mark tasks complete while CI is red.
      See `mandatory_tool_protocols.change_validation` above for the full protocol.
    </verification_protocol>

    <verification_commands>
      <!--
        MANDATORY: After every non-trivial change, run validation BEFORE commit/push.
        Do NOT commit or push until it passes. Do NOT skip steps.

        Local (runs all checks sequentially — lint, scan, test):
          pwsh -NoProfile -File ./scripts/validate.ps1 -All

        This is the SAME script that CI calls with individual switches:
          ./scripts/validate.ps1 -Lint   (CI: lint job)
          ./scripts/validate.ps1 -Scan   (CI: scan job)
          ./scripts/validate.ps1 -Test   (CI: test job)

        If a check is skipped due to a missing local tool, run:
          pwsh -NoProfile -File ./scripts/install-dev-tools.ps1

        | Check                  | Command                                              | When to run              |
        |========================|======================================================|==========================|
        | All (local default)    | ./scripts/validate.ps1 -All                           | Every task               |
        | Lint only              | ./scripts/validate.ps1 -Lint                           | Quick check              |
        | Scan only              | ./scripts/validate.ps1 -Scan                           | Secrets concern          |
        | Test only              | ./scripts/validate.ps1 -Test                           | After lint passes        |
        | Dockerfile/entrypoint  | bash test/test-docker-entrypoint.sh                    | Dockerfile changes       |
      -->
      <rule>When adding a CI workflow check, add its equivalent to scripts/validate.ps1.</rule>
    </verification_commands>

    <post_commit_monitoring>
      After push, monitor CI until green: `gh run list --limit 5`, `gh run watch <id>`, `gh run view <id> --log-failed`.
      If any workflow fails, stop feature work, triage, fix, re-verify, push. Do not mark work complete while CI is failing.
    </post_commit_monitoring>

    <pipeline_speed_policy>
      <lane name="fast_readiness" blocking="true">Build, lint/format, unit tests — keep fast for merge readiness.</lane>
      <lane name="extended_validation" blocking="false">Integration suites, security scans, dependency audits.</lane>
      <rule>Protect the fast lane from slow steps.</rule>
    </pipeline_speed_policy>
  </agent_readiness>

  <validation_before_handoff>
    <step>Run applicable shell tests and verification commands.</step>
    <step>Validate config: `bash test/test-opencode-json.sh` and `bash test/test-compose-config.sh` (there is no `orchestrator-agent.yml` workflow in this repo).</step>
    <step>Summarize: what changed, what was validated, remaining risks (secret-dependent paths, image cache misses).</step>
  </validation_before_handoff>

  <tool_use_instructions>
    <instruction id="sequential_thinking_default_usage" enforcement="MANDATORY">
      <applyTo>*</applyTo>
      <title>Sequential Thinking — MANDATORY for all non-trivial tasks</title>
      <tools><tool>sequential_thinking</tool></tools>
      <guidance>
        **MUST USE** for all non-trivial requests. This is a mandatory protocol, not a suggestion.
        See `mandatory_tool_protocols.sequential_thinking` for full requirements.
        Invoke at: task start (planning), decision points, debugging, and before delegation.
        Skipping this tool on complex tasks is a protocol violation.
      </guidance>
    </instruction>
    <instruction id="memory_default_usage" enforcement="MANDATORY">
      <applyTo>orchestrator</applyTo>
      <title>Persistent Memory — MANDATORY, single-writer (Orchestrator)</title>
      <read_tools>
        <tool>read_graph</tool>
        <tool>search_nodes</tool>
        <tool>open_nodes</tool>
      </read_tools>
      <write_tools available_to="ORCHESTRATOR ONLY">
        <tool>create_entities</tool>
        <tool>create_relations</tool>
        <tool>add_observations</tool>
        <tool>delete_entities</tool>
        <tool>delete_observations</tool>
        <tool>delete_relations</tool>
      </write_tools>
      <guidance>
        **MUST USE** for all non-trivial requests. See `mandatory_tool_protocols.persistent_memory` for full requirements.
        Invoke at: task start (`search_nodes` / `open_nodes`), after significant work (`add_observations` / `create_entities` / `create_relations`),
        and after task completion (persist outcomes and lessons learned — including facts collected from subagent `## Memory Save Requests` lists).
        Skipping memory operations is a protocol violation.
      </guidance>
    </instruction>
    <instruction id="memory_readonly_subagents" enforcement="MANDATORY">
      <applyTo>subagents-and-specialists</applyTo>
      <title>Persistent Memory — READ-ONLY for all non-orchestrator agents</title>
      <read_tools>
        <tool>read_graph</tool>
        <tool>search_nodes</tool>
        <tool>open_nodes</tool>
      </read_tools>
      <guidance>
        You are NOT the Orchestrator, so you are a memory **READER ONLY**. You MAY call
        `search_nodes`, `open_nodes`, and `read_graph` to load context. You MUST NOT call
        `create_entities`, `create_relations`, `add_observations`, or any `delete_*` tool —
        concurrent writes corrupt the memory store. Instead, end your result with a
        `## Memory Save Requests` section listing any durable facts for the Orchestrator to persist.
        Calling a memory write tool is a CRITICAL protocol violation.
      </guidance>
    </instruction>
  </tool_use_instructions>

  <available_tools>
    <summary>
      CLI and runtime tools installed in the orchestrator-service Docker image (see repo `Dockerfile`).
      OpenCode server config lives under `/app`; agent sessions use `/workspace` (pass `--dir /workspace` when attaching).
    </summary>

    <filesystem_layout>
      <path name="/app">OpenCode install and config — `opencode.json`, `AGENTS.md`, `.opencode/`, MCP memory at `/app/.memory/`.</path>
      <path name="/workspace">Agent working directory — clone repos and edit code here; default for `scripts/prompt.ps1`.</path>
    </filesystem_layout>

    <runtimes_and_package_managers>
      <tool name="node" version="24.14.0 LTS">JavaScript runtime. Required for MCP servers launched via `npx`.</tool>
      <tool name="npm">Node package manager (bundled with Node.js).</tool>
      <tool name="npx">Runs MCP packages without a global install (`sequential-thinking`, `memory-graph`).</tool>
      <tool name="python3">System Python 3 interpreter.</tool>
      <tool name="pip">Python package installer (`python3-pip`).</tool>
      <tool name="uv" version="0.10.9">Astral Python package manager; `uvx` runs ephemeral Python tools.</tool>
      <tool name="pwsh" version="7.6.2 LTS">PowerShell 7 — run `scripts/*.ps1` inside the container when needed.</tool>
    </runtimes_and_package_managers>

    <system_utilities>
      <tool name="git">Version control — clone and branch work in `/workspace`.</tool>
      <tool name="rg">Ripgrep — fast content search (also used by OpenCode grep tooling).</tool>
      <tool name="jq">JSON parsing for shell pipelines and `gh api … | jq`.</tool>
      <tool name="curl">HTTP downloads and API probes.</tool>
      <tool name="file">Identify file types by magic bytes.</tool>
      <tool name="make">Build projects that use Makefiles.</tool>
      <tool name="patch">Apply unified diffs outside of `git apply`.</tool>
      <tool name="tar">Archive extract/create (Node and PowerShell installed via tarballs).</tool>
      <tool name="unzip">Extract `.zip` archives.</tool>
      <tool name="xz">Compress/decompress `.xz` archives (`xz-utils`).</tool>
      <tool name="ssh">OpenSSH client — `git clone git@github.com:…` over SSH.</tool>
      <tool name="gpg">GnuPG — verify signatures and handle encrypted artifacts (`gnupg`).</tool>
      <tool name="ps">Process listing (`procps`) — check running MCP or server processes.</tool>
    </system_utilities>

    <cli_tools>
      <tool name="opencode" version="1.18.4">OpenCode CLI — server runs `opencode serve`; agents defined under `.opencode/agents/`.</tool>
      <tool name="gh">GitHub CLI — issues, PRs, repos, Actions. Authenticate with `GH_ORCHESTRATION_AGENT_TOKEN` / `GITHUB_TOKEN` from compose env.</tool>
    </cli_tools>

    <mcp_servers>
      <summary>Configured in `/app/opencode.json` (not separate installs).</summary>
      <tool name="sequential-thinking">Local MCP via `npx @modelcontextprotocol/server-sequential-thinking`.</tool>
      <tool name="memory-graph">Local MCP via `npx @modelcontextprotocol/server-memory`; persists to `/app/.memory/memory.jsonl`. SINGLE-WRITER: only the Orchestrator writes; subagents are read-only (see `persistent_memory` protocol).</tool>
      <tool name="web-reader">Remote MCP at `https://api.z.ai/api/mcp/web_reader/mcp`.</tool>
      <tool name="zread">Remote MCP at `https://api.z.ai/api/mcp/zread/mcp`.</tool>
      <tool name="web-search-prime">Remote MCP at `https://api.z.ai/api/mcp/web_search_prime/mcp`.</tool>
    </mcp_servers>

    <not_installed>
      <summary>Not in this image — do not assume availability inside the container.</summary>
      <item>.NET SDK (`dotnet`) — install in workspace or use a different image if required.</item>
      <item>Bun — use Node/npm/npx instead.</item>
      <item>Docker CLI — container is the runtime, not a Docker host.</item>
    </not_installed>

    <github_authentication>
      <summary>
        GitHub API access (agent `gh`/API + webhook delivery) uses `GH_ORCHESTRATION_AGENT_TOKEN`,
        an org-level PAT with scopes `repo`, `workflow`, `project`, `read:org`. Required for orchestrator
        execution — there is no fallback to `GITHUB_TOKEN`. The webhook itself is HMAC-verified with
        `OS_WEBHOOK_SECRET` (GitHub App or repo webhook).
      </summary>
      <layer name="GH_ORCHESTRATION_AGENT_TOKEN">Org-level PAT (compose env). Exported as `GH_TOKEN`/`GITHUB_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN` so `gh`, MCP, and opencode all authenticate with the same token.</layer>
      <layer name="GITHUB_TOKEN (CI-provided)">Used only for GHCR login in `docker-publish.yml` to push/pull this repo's OWN images. Not used for orchestrator API operations.</layer>
    </github_authentication>

    <scripts_directory>
      <summary>Helper scripts in `scripts/` (host-side wrappers + image entrypoints). Real scripts present:</summary>
      <script name="scripts/prompt.ps1">Non-interactive dispatch wrapper invoked by `webhook_receiver/runner.py` — runs `opencode run --attach …`.</script>
      <script name="scripts/init-project-workspace.ps1">Resolves/creates the per-project `/workspace/<slug>` subdir for a dispatch.</script>
      <script name="scripts/attach.ps1">Interactive attach wrapper (`opencode attach <url>`).</script>
      <script name="scripts/validate.ps1">Runs all local validation (`-All`/`-Lint`/`-Scan`/`-Test`); mirrors CI jobs. Run before every commit.</script>
      <script name="scripts/install-dev-tools.ps1">Installs local dev tools (actionlint, shellcheck, gitleaks, markdownlint) for CI parity.</script>
      <script name="scripts/dc.ps1">docker-compose wrapper used for all compose operations.</script>
      <script name="scripts/docker-entrypoint.sh">orchestratorservice entrypoint — writes `auth.json` from provider env, drops to non-root `app` via gosu.</script>
      <script name="scripts/webhook-entrypoint.sh">webhook-receiver container entrypoint.</script>
      <script name="scripts/git-trust.sh">Configures git safe.directory / trust for mounted workspaces.</script>
      <script name="scripts/import-labels.ps1">Imports labels from `.github/.labels.json` (dispatch labels live in `nam20485/workflow-launch2`).</script>
      <rule>Legacy doc previously listed `devcontainer-opencode.sh`, `start-opencode-server.sh`, `assemble-orchestrator-prompt.sh`, `assemble-local-prompt.sh`, `on-failure-handler.sh`, `run_opencode_prompt.sh`, `resolve-image-tags.sh`, `setup-local-env.sh` — NONE of these exist in this repo.</rule>
    </scripts_directory>
  </available_tools>
</instructions>
