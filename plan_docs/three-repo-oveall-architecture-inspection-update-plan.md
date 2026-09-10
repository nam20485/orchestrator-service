# Three-Repo Software-Factory Architecture: Inspection Findings & Update Plan

> Status: **DRAFT PLAN — awaiting approval before edits.**
> Scope: bring **this repo** (`orchestrator-service`) instruction/context files + memory up to date with the *actual* multi-repo architecture.
> Sources: direct inspection of `intel-agency/agent-context`, `nam20485/workflow-launch2`, and this repo (all local), cross-referenced with the live webhook→dispatch code path.

---

## 1. Executive summary

`orchestrator-service` is one node in a **software factory** of cooperating artifacts:

| # | Artifact | Repo | Role |
|---|---|---|---|
| 1 | **Template** | `intel-agency/agent-context` | The GitHub template cloned per app; carries local opencode agents + memory/rules + the `gh-issue-tracking-init` skill |
| 2 | **Factory** | `nam20485/workflow-launch2` | Stamps instances from the template, seeds `plan_docs/`, imports labels, files the dispatch issue |
| 3 | **Runtime** | `orchestrator-service` (this repo) | docker-compose opencode webhook runtime that receives events and dispatches non-interactive prompts |
| 4 | **Instances** | `intel-agency/<app>-<suffix>` (e.g. `gap-miner-v2-tango85`) | Cloned+seeded repos the runtime implements against |
| 5 | **Workflow store** | `nam20485/agent-instructions` | Canonical dynamic-workflow definitions, fetched fresh by the runtime at dispatch time |

**The core problem:** this repo's instruction/context files describe a **stale, abandoned architecture** — a *GitHub-Actions + devcontainer + .NET/Aspire + external-prebuild* model inherited from the old template/factory flow. That framing does not match the actual **docker-compose + Python-webhook + self-built-image** runtime. The most damaging stale doc is `image/.opencode/AGENTS.md`, because it is baked into the container and becomes the **running agents' context**.

---

## 2. Coherent architecture (findings)

### 2.1 The dispatch contract — GitHub issues as a message bus

There is **no in-repo workflow/devcontainer/webhook code** in the template or instances. Cross-system coupling is done entirely through **labeled GitHub issues** + a **GitHub repo webhook** pointed at the runtime:

```
[workflow-launch2]                         [instance repo]                 [orchestrator-service]
create-repo-agent-context.ps1              gap-miner-v2-tango85            webhook_receiver (FastAPI :8080)
   │  clone intel-agency/agent-context         │                              │
   │  seed plan_docs/<slug>/                   │   ① issue:labeled            │
   │  Class-2 cleanup                          │      label = gh-issue-        │  ② HMAC verify
   │  import .labels.json                      │        tracking:direct-body   │     match (filters.py)
   │  file dispatch issue ────────────────────▶│      body  = /gh-issue-       │  ③ build prompt (prompts.py)
   │                                           │        tracking-init          │  ④ dispatch_to_opencode()
   └─► push seed commit                        │   ───── GitHub webhook ──────▶│     prompt.ps1 → opencode run
                                               │                              │     --attach :4099 --dir /workspace/<slug>
                                               │                              │     --agent orchestrator --dangerously-skip-permissions
                                               │   ◄── runner posts status/    │  ⑤ IdleWatchdog classifies run
                                               │       failure comments        │     (completed/failed/idle_timeout/zero-work)
```

### 2.2 Each repo's real role (verified)

**`intel-agency/agent-context` (template).** Infrastructure substrate, **not** the dispatch runtime.
- Ships its OWN `.opencode/agents/` (orchestrator + 7 specialists) + `.agents/` (memory.md, rules/, the one vendored skill `gh-issue-tracking-init`) for **local interactive** opencode use.
- Contains **no** `.github/workflows/`, **no** `.devcontainer/`, **no** webhook code, **no** `plan_docs/`, **no** README.
- Identity anchor at `AGENTS.md:5` (`**GitHub template repo**`) is the clone-time rewrite target → `**project instance** cloned from the intel-agency/agent-context GitHub template`.
- Note: `.opencode/opencode.jsonc:7` `small_model: google/gemini-3.5-flash` is dead config (Google provider unreachable) — out of scope here but worth flagging upstream.

**`nam20485/workflow-launch2` (factory).** Entry point `scripts/create-repo-agent-context.ps1` chains:
1. `create-repo-with-plan-docs.ps1` (engine): `gh repo create --template intel-agency/agent-context`, name `<Slug>-<NATO-suffix>`, seed `plan_docs/<slug>/` (flattened), create secret `GEMINI_API_KEY` + var `VERSION_PREFIX`, clone, **placeholder replace** (`agent-context`→repo name in contents+paths; `intel-agency`→owner if changed; AGENTS.md anchor rewrite), seed commit+push (handles template race via rebase+amend+force-with-lease).
2. `cleanup-template-state.ps1` (Class-2): blank `.agents/memory.md`, delete `docs/plans/.completed`+`.deferred`, drop `run-issues-review/`.
3. `import-labels.ps1`: import full label set from `.github/.labels.json`.
4. `trigger-gh-issue-tracking-init.ps1`: file issue **label `gh-issue-tracking:direct-body`**, **body `/gh-issue-tracking-init`**.
- `plan_docs/` holds **48 app slugs** (e.g. `gap-miner-v2`, `accp`, `profile-genie`, `Helix3D`…).
- `.github/.labels.json` is the source of `gh-issue-tracking:direct-body`, `orchestration:dispatch` (legacy), and the lifecycle labels.
- `create-repo-from-slug.ps1` / `ai-new-workflow-app-template` / `trigger-project-setup.ps1` (`orchestration:dispatch` + `/orchestrate-dynamic-workflow`) are the **legacy, superseded** path.

**`orchestrator-service` (this repo / runtime).** docker-compose, **self-built** images:
- 3 services: `orchestratorservice` (`opencode serve` :4099), `webhook-receiver` (FastAPI :8080), `webhook-proxy` (Caddy :80). `Dockerfile:116` `COPY image/ /app/`; `Dockerfile:124-128` installs `image/.opencode/` → `/home/app/.config/opencode/` (global config) so `opencode serve` auto-loads it.
- Dispatch path: `app.py` (HTTP gate) → `filters.py:97-138` (`should_dispatch`: event `issues` + action `labeled` + label prefix `gh-issue-tracking:`/`orchestration:`; `direct-body` gated by `DIRECT_BODY_ALLOWED_SENDERS`) → `prompts.py` (Jinja2 render of `orchestration_prompt.jinja2.md` match-clauses) → `runner.py:715` `dispatch_to_opencode()` → `scripts/prompt.ps1` (`opencode run --attach … --dangerously-skip-permissions`) → `IdleWatchdog` classifies the run.
- Builds its OWN GHCR image (`docker-publish.yml`); **no** external prebuild repo.

**`nam20485/agent-instructions` (workflow store).** Canonical dynamic-workflow definitions (`project-setup`, `create-epic-v2`, `implement-epic`, `review-epic-prs`); fetched fresh per assignment. (Consistent with current docs — no staleness.)

### 2.3 Known pipeline gap (context, not necessarily in scope)
After `/gh-issue-tracking-init` builds the issue hierarchy, **nothing currently drives implementation**. `workflow-launch2/docs/orchestration-cycle-label-trigger-options.md` recommends a terminal skill-completion label + a new match case. Flagging for awareness.

---

## 3. Staleness inventory (this repo only)

### 3.1 `image/.opencode/AGENTS.md` — **STALE / highest impact** (the running agents' context)
Installed to `/home/app/.config/opencode/AGENTS.md`; loaded by every agent session (`opencode.json` → `instructions: ["AGENTS.md"]`). Its **top half** describes the abandoned GHA/devcontainer model:

| Line(s) | Stale claim | Reality |
|---|---|---|
| `:10-14` | "GitHub Actions-based… `orchestrator-agent` workflow… spins up a prebuilt devcontainer" | docker-compose webhook → `prompt.ps1` |
| `:20-21,33` | "This repository is a **GitHub template repo**"; `gh repo create --template <org>/orchestrator-service` | It is a **runtime**; factory is `workflow-launch2`, template is `intel-agency/agent-context` |
| `:35` | Secrets `ZHIPU_API_KEY`/`KIMI_CODE_…`/`OPENAI_API_KEY` | Runtime uses `ZAI_CODING_API_KEY`/`OPENROUTER_API_KEY`/`MODEL_STUDIO_API_KEY` |
| `:41,47,60,77,133-136,163` | External `<org>/orchestrator-service-prebuild` repo supplies devcontainer/Dockerfile | This repo **builds its own** images from its own `Dockerfile` |
| `:61` | ".NET SDK 10 + Aspire + Avalonia templates, Bun" | None installed (self-contradicts its own `<not_installed>` at `:466-471`) |
| `:68,70,77,79-82,357,485-489` | `orchestrator-agent.yml`, `prompts/orchestrator-agent-prompt.md`, `.devcontainer/`, port **4096**, `start-opencode-server.sh`, `devcontainer-opencode.sh`, `assemble-orchestrator-prompt.sh`, `run_opencode_prompt.sh` | **None exist**; port is **4099**; prompt assembly is Python Jinja2 |
| `:162` | ".opencode/ checked out by actions/checkout; do not COPY in Dockerfile" | `Dockerfile:116` DOES `COPY image/ /app/` |

✅ **Keep** the accurate bottom half: `<mandatory_tool_protocols>` (sequential-thinking, single-writer memory, change-validation) and `<available_tools>` (Node 24.14.0, uv, pwsh 7.6.2, opencode 1.18.4, `/app` vs `/workspace`, non-root).

### 3.2 Repo-root `AGENTS.md` — accurate core, **stale Greater-System + dangling refs**
- **Greater System (§42-60):**
  - `:46-47` factory scripts named `create-repo-from-slug.ps1` → `create-repo-with-plan-docs.ps1`; template = `intel-agency/ai-new-workflow-app-template` → **STALE**. Correct: `create-repo-agent-context.ps1` (canonical) → `create-repo-with-plan-docs.ps1` (engine); template = `intel-agency/agent-context`.
  - `:57-58` "How a new project is created": old trigger `trigger-project-setup.ps1` + label `orchestration:dispatch` + `/orchestrate-dynamic-workflow`. **STALE.** Correct: `trigger-gh-issue-tracking-init.ps1` + `gh-issue-tracking:direct-body` + `/gh-issue-tracking-init`, preceded by Class-2 cleanup + label import.
  - `:51-53` `agent-instructions` → **accurate**.
- **Dangling references:**
  - `:23` "container uses `image/AGENTS.md`" → **`image/AGENTS.md` does not exist**; real agent context is `image/.opencode/AGENTS.md` → `/home/app/.config/opencode/AGENTS.md`.
  - `:20, :64` cite `plan_docs/agent-loop-refactor/architecture.md` + `application_plan.md` as "authoritative" → **directory does not exist**.
  - `:114` cites `docs/agent-loop-dev-plans/` → **does not exist**.

### 3.3 `README.md` — accurate core, **two dangling refs**
- `:7` cites `plan_docs/agent-loop-refactor/architecture.md` (absent).
- `:201` cites `docs/agent-loop-dev-plans/` (absent).

### 3.4 Accurate (no change needed)
`GEMINI.md`, `docs/` (10 current operational docs), `image/.opencode/opencode.json`, the recently-added subagent-`external_directory` permission bullet (`AGENTS.md:29`).

---

## 4. Planned updates (proposed — for approval)

### 4a. Instruction / context files

**P1 — Rewrite `image/.opencode/AGENTS.md` (operative agent context).**
Replace the GHA/devcontainer/.NET/external-prebuild top half with an accurate description of: the docker-compose runtime, the multi-repo factory (template/factory/runtime/instances/agent-instructions), the **issue-as-message-bus** dispatch contract (`gh-issue-tracking:direct-body` → webhook → `prompt.ps1`), the real secrets/provider env vars, port **4099**, and the self-built image. **Preserve** the accurate `<mandatory_tool_protocols>` + `<available_tools>` sections verbatim. *(Largest, highest-value edit.)*

**P2 — Correct repo-root `AGENTS.md` "Greater System" + dangling refs.**
- `:46-47` → factory entry `create-repo-agent-context.ps1` + engine `create-repo-with-plan-docs.ps1`; template `intel-agency/agent-context`.
- `:57-58` → current trigger flow (cleanup → label import → `gh-issue-tracking:direct-body` + `/gh-issue-tracking-init`).
- `:23` → `image/.opencode/AGENTS.md` (not `image/AGENTS.md`).
- `:20,:64` → drop/replace the nonexistent `plan_docs/agent-loop-refactor` "authoritative" pointer (point at real docs or remove).
- `:114` → remove `docs/agent-loop-dev-plans` ref.

**P3 — Fix `README.md` dangling refs** (`:7`, `:201`).

### 4b. Memory (Kilo project memory)

**P4 — Add durable architecture fact** `three_repo_software_factory_architecture`:
> Template = `intel-agency/agent-context`; Factory = `nam20485/workflow-launch2` (entry `scripts/create-repo-agent-context.ps1`); Runtime = `orchestrator-service` (docker-compose: opencode serve :4099 + webhook-receiver FastAPI :8080 + Caddy :80, self-built image); Instances = `intel-agency/<app>-<suffix>`; Workflow store = `nam20485/agent-instructions`. Dispatch contract = GitHub issues as message bus: factory files `gh-issue-tracking:direct-body` issue (body `/gh-issue-tracking-init`) → webhook → `webhook_receiver` matches (`filters.py`) → `dispatch_to_opencode()` → `prompt.ps1` → `opencode run --attach`. Legacy path (`create-repo-from-slug.ps1`, `ai-new-workflow-app-template`, `orchestration:dispatch`, `/orchestrate-dynamic-workflow`) is superseded.

**P5 — Audit/supersede stale memory** referencing the old template/factory flow (e.g. `gh_issue_tracking_labels_external_repo` already notes labels live in workflow-launch2 — confirm consistent; correct any entry naming `ai-new-workflow-app-template` or `create-repo-from-slug` as current).

---

## 5. Implementation order & verification

1. **P4/P5 (memory)** — lowest risk, immediate correctness benefit; do first.
2. **P2 (repo-root AGENTS.md)** — surgical line edits; run `bash test/test-opencode-json.sh` + `pwsh -NoProfile -File ./scripts/validate.ps1 -Lint`.
3. **P3 (README)** — two line edits.
4. **P1 (image/.opencode/AGENTS.md)** — full rewrite of stale half; re-validate + sanity-check frontmatter/markdown.
5. Commit each as a focused change; push; rebuild image (CI `docker-publish.yml`) so the running-agent context updates.

**Verify the running context** post-rebuild: `docker run --rm --entrypoint cat <image> /home/app/.config/opencode/AGENTS.md | head` confirms the new content is baked in.

---

## 6. Open questions / decisions

1. **P1 scope:** full rewrite of `image/.opencode/AGENTS.md` (recommended) vs. surgical corrections only? (Rewrite is cleaner; the file is ~half-wrong.)
2. **Authoritative-architecture pointer:** with `plan_docs/agent-loop-refactor/` gone, what should `AGENTS.md:64` cite instead? Options: `GEMINI.md`, `docs/deployment-compose.md`, or a new short architecture doc. (Recommend `docs/deployment-compose.md` + this file.)
3. **Pipeline gap (§2.3):** in scope to document the missing implementation trigger, or out of scope for this pass?
4. **`image/.opencode/AGENTS.md` provenance:** it appears copied from the old template. Confirm we own/should edit it here (yes — it ships from this repo's `image/`).

---

## 7. Summary of corrections (the "what was stale" answer)

| Was (stale) | Is (correct) |
|---|---|
| template = `intel-agency/ai-new-workflow-app-template` | `intel-agency/agent-context` |
| factory entry = `create-repo-from-slug.ps1` | `create-repo-agent-context.ps1` |
| trigger = `orchestration:dispatch` + `/orchestrate-dynamic-workflow` | `gh-issue-tracking:direct-body` + `/gh-issue-tracking-init` |
| runtime = GitHub Actions + devcontainer + external prebuild | docker-compose (opencode serve + FastAPI webhook + Caddy), self-built image |
| stack includes .NET/Aspire/Avalonia/Bun | Python (FastAPI) + opencode; no .NET/Bun |
| port 4096 | 4099 |
| secrets ZHIPU/KIMI/OPENAI | ZAI_CODING/OPENROUTER/MODEL_STUDIO |
| this repo "is a GitHub template" | this repo is the runtime |
