---
file: AGENTS.md
description: Project instructions for coding agents
scope: repository
---

<instructions>
  <purpose>
    <summary>
      GitHub Actions-based AI orchestration system. On GitHub events (currently: issues labeled,
      workflow_dispatch), the `orchestrator-agent` workflow assembles a structured prompt containing
      the event type, actor, metadata, and raw event payload. It then spins up a prebuilt devcontainer
      and runs `opencode --agent orchestrator`, which analyzes the prompt against a set of matching
      cases and delegates the appropriate work to specialist sub-agents in `.opencode/agents/`.
    </summary>
  </purpose>

  <template_usage>
    <summary>
      This repository is a **GitHub template repo** (`<org>/orchestrator-service`).
      New project repositories are created from it using automation scripts in the
      `<org>/orchestrator-launch` repo. The scripts clone this template, seed plan docs,
      replace template placeholders, and push — producing a ready-to-go AI-orchestrated repo.
    </summary>

    <template-clone-instances>
      Once the template has been cloned into a new instance, this file must be updated to match the new repo's specifics (e.g., name, links, instructions). 
    </template-clone-instances>

    <creation_workflow>
      <step>1. Run `./scripts/create-repo-from-slug.ps1 -Slug &lt;project-slug&gt; -Yes` from the `orchestrator-launch` repo.</step>
      <step>2. That delegates to `./scripts/create-repo-with-plan-docs.ps1` which:
        - Creates a new GitHub repo from this template via `gh repo create --template <org>/orchestrator-service`
        - Generates a random suffix for the repo name (e.g., `project-slug-bravo84`)
        - Creates repo secrets (`ZHIPU_API_KEY`, `KIMI_CODE_ORCHESTRATOR_AGENT_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GH_ORCHESTRATION_AGENT_TOKEN`)
        - Clones the new repo locally
        - Copies plan docs from `./plan_docs/&lt;slug&gt;/` into the clone's `plan_docs/` directory
        - Replaces all template placeholders (`orchestrator-service` → new repo name, `<org>` → new owner)
        - Commits and pushes the seeded repo
      </step>
      <step>3. On push, the clone's `validate` workflow runs CI (lint, scan, tests). The prebuilt devcontainer image is sourced from the external `<org>/orchestrator-service-prebuild` repo — no `publish-docker` or `prebuild-devcontainer` workflows exist in this template repo.</step>
    </creation_workflow>

    <template_design_constraints>
      <rule>Template placeholders (`orchestrator-service`, `<org>`) in file contents and paths are replaced by the creation script. Keep them consistent.</rule>
      <rule>The `plan_docs/` directory contains external-generated documents seeded at clone time. Exclude it from strict linting (markdown lint, etc.).</rule>
      <rule>The consumer `.devcontainer/devcontainer.json` references the prebuilt GHCR image from `<org>/orchestrator-service-prebuild`. The Dockerfile and prebuild pipeline live in that external repo, not here.</rule>
    </template_design_constraints>

    <automation_scripts>
      <entry><repo>&lt;org&gt;/orchestrator-launch</repo><path>scripts/create-repo-from-slug.ps1</path><description>Entry point — takes a slug, resolves plan docs dir, delegates to create-repo-with-plan-docs.ps1</description></entry>
      <entry><repo>&lt;org&gt;/orchestrator-launch</repo><path>scripts/create-repo-with-plan-docs.ps1</path><description>Full pipeline: repo create, clone, seed docs, placeholder replace, commit, push</description></entry>
    </automation_scripts>
  </template_usage>

  <tech_stack>
    <item>opencode CLI — agent runtime (`opencode --model zai-coding-plan/glm-5.2 --variant max --agent orchestrator`)</item>
    <item>Z.AI GLM models (`glm-5.2` default, `glm-5`, `glm-5.1`, `glm-4.7`, `glm-4.5-air`) via `ZAI_CODING_API_KEY`</item>
    <item>Google Gemini models (`gemini-3.1-pro-preview`, `gemini-3.1-flash-lite-preview`, etc.) via `GEMINI_API_KEY`</item>
    <item>GitHub Actions — workflow trigger and runner; prebuilt devcontainer from `<org>/orchestrator-service-prebuild`</item>
    <item>.NET SDK 10 + Aspire + Avalonia templates, Bun, uv (all in devcontainer, sourced from external prebuild image)</item>
    <item>MCP servers (enabled): `@modelcontextprotocol/server-sequential-thinking`, `@modelcontextprotocol/server-memory` (knowledge-graph persistent memory; single-writer protocol — see `mandatory_tool_protocols.persistent_memory`).</item>
    <item>MCP servers (disabled): `@modelcontextprotocol/server-github`, `https://mcp.grep.app`</item>
  </tech_stack>

  <repository_map>
    <!-- Workflows -->
    <entry><path>.github/workflows/orchestrator-agent.yml</path><description>Primary workflow — assembles prompt, pulls prebuilt devcontainer image, runs opencode orchestrator. Triggers: `issues: [labeled]`, `workflow_dispatch`. Caches knowledge graph memory in `.memory/` via `actions/cache`.</description></entry>
    <entry><path>.github/workflows/validate.yml</path><description>CI validation — jobs: `lint` (actionlint, gitleaks, markdownlint), `scan` (gitleaks), `test` (bash + Pester test suite).</description></entry>
    <entry><path>.github/workflows/prompts/orchestrator-agent-prompt.md</path><description>Prompt template with `__EVENT_DATA__` placeholder (sed-substituted at runtime)</description></entry>
    <!-- Agent definitions -->
    <entry><path>.opencode/agents/orchestrator.md</path><description>Orchestrator — coordinates specialists, never writes code directly. Enforces delegation-depth ≤2.</description></entry>
    <entry><path>.opencode/agents/</path><description>11 specialist agents: code-reviewer, debugger, developer, documentation-expert, github-expert, odbplusplus-expert, orchestrator, planner, qa-test-engineer, researcher, security-expert.</description></entry>
    <entry><path>.opencode/commands/</path><description>21 reusable command prompts including: orchestrate-new-project, grind-pr-reviews, fix-failing-workflows, create-application, create-app-plan, plan-app, orchestrate-dynamic-workflow, orchestrate-project-setup, resolve-pr-comments, optimize-prompt, and more.</description></entry>
    <entry><path>opencode.json</path><description>opencode config (root level) — multi-provider model definitions (ZhipuAI, OpenAI, Kimi, Google), default model, MCP server definitions, and tool permissions.</description></entry>
    <!-- Devcontainer -->
    <entry><path>.devcontainer/devcontainer.json</path><description>Consumer devcontainer — pulls prebuilt GHCR image `ghcr.io/<org>/orchestrator-service-prebuild/devcontainer:main-latest`, forwards port 4096, auto-starts `opencode serve` via `scripts/start-opencode-server.sh` on container start.</description></entry>
    <!-- Scripts -->
    <entry><path>scripts/start-opencode-server.sh</path><description>Guarded `opencode serve` bootstrapper used by the devcontainer lifecycle and workflow attach path. Uses `setsid` to survive devcontainer exec session teardown.</description></entry>
    <entry><path>scripts/devcontainer-opencode.sh</path><description>Primary CLI wrapper for devcontainer-based orchestration. Supports subcommands for one-shot prompt execution and server attach mode. Used by the `orchestrator-agent` workflow.</description></entry>
    <entry><path>scripts/assemble-orchestrator-prompt.sh</path><description>Assembles the orchestrator prompt from the template, event context, and event JSON. Writes to `.assembled-orchestrator-prompt.md`.</description></entry>
    <entry><path>run_opencode_prompt.sh</path><description>Root-level script — validates API keys, exports `GH_TOKEN`/`GITHUB_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN` from `GH_ORCHESTRATION_AGENT_TOKEN`, and invokes `opencode run --model zai-coding-plan/glm-5.2 --variant max --agent orchestrator` in server attach mode.</description></entry>
    <!-- Tests -->
    <entry><path>test/</path><description>Test suite — shell scripts (`bash`) and Pester (`pwsh`) tests: devcontainer tool availability, prompt assembly, image tag logic, opencode run/server, watchdog IO detection, and workflow/agent validation.</description></entry>
    <entry><path>test/fixtures/</path><description>Sample webhook payloads for local testing (issues-opened, pr-opened, pr-review-submitted, etc.) and prompt fixtures.</description></entry>
    <!-- Skills -->
    <entry><path>.agents/skills/</path><description>Reusable agent skills: `forensic-analysis-report` (workflow failure analysis), `orchestration-run-analysis` (post-mortem reports), `prompt-bisect` (constraint bisection via git worktrees).</description></entry>
    <!-- Remote instructions -->
    <entry><path>local_ai_instruction_modules/</path><description>Local instruction modules (development rules, workflows, delegation, terminal commands)</description></entry>
    <!-- Docs -->
    <entry><path>docs/</path><description>Developer documentation: agent model assignments, orchestration migration options, workflow issues and fixes, subagent tracing guides, and quickstart docs.</description></entry>

    <opencode_server>
      <summary>
        The consumer devcontainer auto-starts `opencode serve` through `scripts/start-opencode-server.sh`
        (using `setsid` to survive devcontainer exec session teardown).
        The server listens on port `4099` by default so host or in-container clients can attach with
        `opencode run --attach http://127.0.0.1:4099 ...` (or the forwarded host port when connecting from outside the container).
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
      <item>`ZHIPU_API_KEY` — ZhipuAI GLM model access; set in repo Settings → Secrets.</item>
      <item>`KIMI_CODE_ORCHESTRATOR_AGENT_API_KEY` — Kimi (Moonshot) model access; set in repo Settings → Secrets.</item>
      <item>`OPENAI_API_KEY` — OpenAI model access; set in repo Settings → Secrets.</item>
      <item>`GEMINI_API_KEY` — Google Gemini model access (mapped to `GOOGLE_GENERATIVE_AI_API_KEY` in the devcontainer); set in repo Settings → Secrets.</item>
      <item>`GH_ORCHESTRATION_AGENT_TOKEN` — org-level PAT with scopes: repo, workflow, project, read:org. Required for orchestrator execution. No fallback to `GITHUB_TOKEN`.</item>
      <item>`GITHUB_TOKEN` — provided automatically by Actions; used only for GHCR login (image pull).</item>
    </secrets>
    <devcontainer_image>
      The devcontainer image is sourced from the external `<org>/orchestrator-service-prebuild` repo.
      Image: `ghcr.io/<org>/orchestrator-service-prebuild/devcontainer:main-latest`.
      Login via `docker/login-action` with `GITHUB_TOKEN`. There are no `publish-docker` or `prebuild-devcontainer`
      workflows in this repo — the Dockerfile and prebuild pipeline live in the external prebuild repo.
    </devcontainer_image>
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
    <rule>`.opencode/` is checked out by `actions/checkout`; do not COPY it in the Dockerfile.</rule>
    <rule>The Dockerfile and prebuild pipeline live in the external `<org>/orchestrator-service-prebuild` repo. Consumer devcontainer uses `"image:"` pointing to `ghcr.io/<org>/orchestrator-service-prebuild/devcontainer:main-latest` — no local build in this repo.</rule>
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
        <command purpose="devcontainer">bash test/test-devcontainer-tools.sh</command>
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
    <rule>Prompt assembly pipeline:
      1. Read template from `.github/workflows/prompts/orchestrator-agent-prompt.md`.
      2. Prepend structured event context (event name, action, actor, repo, ref, SHA).
      3. Append raw event JSON from `${{ toJson(github.event) }}`.
      4. Write to `.assembled-orchestrator-prompt.md` and export path via `GITHUB_ENV`.
      5. Workflow invokes opencode via `scripts/devcontainer-opencode.sh prompt -f "$ORCHESTRATOR_PROMPT_PATH"`.
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
        | Devcontainer tests     | bash test/test-devcontainer-tools.sh                   | Dockerfile changes       |
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
    <step>Validate workflow YAML: `grep -c "^name:" .github/workflows/orchestrator-agent.yml  # expect 1`</step>
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
        GitHub API access uses a single token: `GH_ORCHESTRATION_AGENT_TOKEN`, an org-level PAT
        with scopes `repo`, `workflow`, `project`, `read:org`. This token is required for
        orchestrator execution — there is no fallback to `GITHUB_TOKEN`.
      </summary>
      <layer name="GH_ORCHESTRATION_AGENT_TOKEN">Org-level PAT configured as a repo/org secret. `run_opencode_prompt.sh` exports it as `GH_TOKEN`, `GITHUB_TOKEN`, and `GITHUB_PERSONAL_ACCESS_TOKEN` so that `gh` CLI, MCP GitHub server, and opencode all authenticate with the same token.</layer>
      <layer name="GITHUB_TOKEN (Actions-provided)">Only used for GHCR login (`docker/login-action`) to pull devcontainer images. Not used for orchestrator API operations.</layer>
    </github_authentication>

    <scripts_directory>
      <summary>Helper scripts in `scripts/` for orchestration, GitHub setup, and management tasks.</summary>
      <script name="scripts/devcontainer-opencode.sh">Primary CLI wrapper for devcontainer-based orchestration. Subcommand-based: runs one-shot prompts or attaches to a running opencode server. Used by the `orchestrator-agent` workflow.</script>
      <script name="scripts/start-opencode-server.sh">Guarded `opencode serve` bootstrapper. Uses `setsid` to create a new session that survives devcontainer exec teardown.</script>
      <script name="scripts/assemble-orchestrator-prompt.sh">Assembles and writes the structured orchestrator prompt from the template + event context.</script>
      <script name="scripts/assemble-local-prompt.sh">Assembles prompts for local (non-Actions) execution.</script>
      <script name="scripts/on-failure-handler.sh">Posts failure label and comment on the triggering issue when the orchestrator workflow fails.</script>
      <script name="scripts/validate.ps1">Runs all local validation checks (`-All`, `-Lint`, `-Scan`, `-Test`). Mirrors CI jobs. Run before every commit.</script>
      <script name="scripts/install-dev-tools.ps1">Installs local development tools (actionlint, shellcheck, gitleaks, markdownlint, etc.) needed for full local validation parity with CI.</script>
      <script name="scripts/common-auth.ps1">Shared `Initialize-GitHubAuth` function — checks `gh auth status`, authenticates via PAT token (`$env:GITHUB_AUTH_TOKEN`) or interactive login.</script>
      <script name="scripts/gh-auth.ps1">Extended GitHub auth helper — supports PAT token auth via `--with-token` and interactive fallback.</script>
      <script name="scripts/import-labels.ps1">Imports labels from `.github/.labels.json` into the repository.</script>
      <script name="scripts/create-milestones.ps1">Creates project milestones from plan docs.</script>
      <script name="scripts/create-project.ps1">Creates GitHub project boards.</script>
      <script name="scripts/create-dispatch-issue.ps1">Creates workflow dispatch issues for triggering the orchestrator.</script>
      <script name="scripts/test-github-permissions.ps1">Verifies `GITHUB_TOKEN` has required permissions (contents, issues, PRs, packages).</script>
      <script name="scripts/query.ps1">PR review thread manager — fetches unresolved review threads from a PR, summarizes them, and can batch-reply and resolve them. Supports `--AutoResolve`, `--DryRun`, `--Interactive`, `--ReplyEach`, `--Path`, `--BodyContains` filtering. Use this instead of writing ad-hoc scripts to resolve PR review comments.</script>
      <script name="scripts/collect-trace-artifacts.sh">Collects and archives opencode subagent trace artifacts.</script>
      <script name="scripts/resolve-image-tags.sh">Resolves the correct devcontainer image tag to use at runtime.</script>
      <script name="scripts/setup-local-env.sh">Sets up a local development environment (env vars, tool checks).</script>
      <script name="scripts/update-remote-indices.ps1">Updates remote instruction module indices.</script>
    </scripts_directory>
  </available_tools>
</instructions>
