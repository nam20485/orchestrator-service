# Discovery Path Alignment — Bring the Webhook Orchestrator to the Golden Path

**Status:** Draft for approval
**Date:** 2026-07-03
**Scope:** Align the `orchestrator-service` webhook-dispatch discovery path with the proven
GitHub-Actions "golden path" (`intel-agency/workflow-orchestration-queue-foxtrot54`).

> **Decision (recorded):** Stay on the `main` branch of `nam20485/agent-instructions`.
> The `optimization` branch trims ~9,050 lines of context filler but is **35 commits behind `main`**
> and is missing the entire modern `project-setup` workflow (6 assignments, 4-step orchestration,
> `pr-approval-and-merge`, branch-protection import, `orchestration:plan-approved`). It is stale and
> unvalidated. This document therefore targets `main` only. No branch-cutover work is in scope.

---

## 1. Executive summary

The webhook orchestrator (`orchestrator-service`) and the golden-path GHA orchestrator run the **same
branching-logic prompt** and ship the **same local discovery index** (`local_ai_instruction_modules/ai-dynamic-workflows.md`).
The proven runs (foxtrot54, weekly-green since 2026-04-27) and the stalled run (lima63) differ in one
thing: **foxtrot54's agent read the local index first and then executed the workflow directly; lima63's
agent never read the local index, hit a `Skill not found` error at execution time, and fell back to
fragile "manual orchestration" that stalled after 2 of 6 assignments.**

The asset that fixes this already exists in the image and in every cloned workspace. The work is to make
the golden-path behavior **deterministic** (prompt-enforced) instead of **judgment-dependent** (which
varies by model and opencode version).

This document specifies a tiered set of minor-to-moderate changes, ranked by leverage.

---

## 2. Background — terminology and why it matters

The user's operational rule, hard-won across many orchestration runs:

> *"Little changes that seem innocuous can derail the whole agent prompt run."*

The discovery path is exactly such a sensitive surface. Three terms are routinely conflated and must be
kept distinct, because the opencode runtime resolves them differently depending on version:

| Term | What it is | Where it lives | Resolved by |
|---|---|---|---|
| **Copilot "prompt"** | GitHub Copilot Chat's slash commands (`/name`). The **original** form of all these orchestrator entry points. | `.github/prompts/*.prompt.md` | Copilot Chat client |
| **opencode "command"** | opencode's equivalent of a slash command — a prompt template loaded on `/name`. | `.opencode/commands/*.md` | opencode CLI command resolver |
| **opencode "skill"** | A **separate, newer** mechanism: the `skill` tool + skill registry (`SKILL.md` manifests). | `.agents/skills/`, global/bundled skills | opencode `skill` tool resolver |

The prompt text inherited from the Copilot-prompt era writes the directive as:

```
/orchestrate-dynamic-workflow
    $workflow_name = project-setup
```

This `/name` notation was unambiguous in the Copilot-prompt and old-opencode-command eras. But on
**opencode 1.15.x** (what lima63 ran), a `/name` token in the prompt is routed to the **skill resolver
first** — and because `orchestrate-dynamic-workflow` is registered as a *command*
(`.opencode/commands/orchestrate-dynamic-workflow.md`), not a *skill*, the resolver throws
`Skill "…" not found`.

**This is the innocuous change that derailed lima63.** The golden path ran on opencode 1.2.24, whose
`/name` resolution did not collide with a skill registry. Same prompt text, different runtime behavior.

> Supporting evidence: `image/.opencode/commands/orchestrate-dynamic-workflow.md` exists (it is a
> command); no skill named `orchestrate-dynamic-workflow` exists. lima63 error at log L408–409:
> *"Skill 'orchestrate-dynamic-workflow' not found. Available skills: customize-opencode,
> forensic-analysis-report, orchestration-run-analysis, perfect-idea, plan-app, plan-to-beads,
> prompt-bisect, validate-and-commit."*

---

## 3. The proven discovery mechanism (golden path — foxtrot54)

Reference run: `intel-agency/workflow-orchestration-queue-foxtrot54` → `orchestrator-agent`
run `24971284567` (job `73114674392`, ✅ success, 2026-04-27, ~1.5h, completed all 6 assignments +
`pr-approval-and-merge`). The repo has run this chain **weekly since Apr 27, every run green**, and even
filed debrief issues (#3 secret leak, #4–#9 cleanup).

Discovery chain (from `0_orchestrate.txt`):

```
1. Read LOCAL  local_ai_instruction_modules/ai-dynamic-workflows.md
      ↳ checked-in index: every dynamic workflow's shortId + raw URL + canonical path
      ↳ "Agents MUST resolve dynamic workflows from the remote canonical repository."
   → learns: project-setup exists; raw URL = …/dynamic-workflows/project-setup.md
2. WebFetch that ONE raw URL        → the workflow definition (6 assignments + events)
3. WebFetch each assignment def      → create-workflow-plan, init-existing-repository,
                                      create-app-plan, … as each is needed
4. Execute assignments IN ORDER, delegating to specialists.
```

Log evidence (verbatim):
- `→ Read local_ai_instruction_modules/ai-dynamic-workflows.md` (L933)
- `% WebFetch …/dynamic-workflows/project-setup.md` (L940)
- sequential per-assignment WebFetch (L1070, L1244, L1357, L1454, L1552, L1635, L1715)

**One local read, then only targeted fetches. No skill resolution. No command-file indirection.
No discovery subagent.** The agent treated `/orchestrate-dynamic-workflow` as a *directive to execute*
and resolved the definition through the local index.

### The local index itself

`image/local_ai_instruction_modules/ai-dynamic-workflows.md` is the canonical discovery asset. It
declares the repo, the branch, the directory, and lists every workflow with its GitHub-UI and raw URLs:

```markdown
# Dynamic Workflows Index
Repository: nam20485/agent-instructions
Branch: main
Directory: ai_instruction_modules/ai-workflow-assignments/dynamic-workflows/

#### project-setup
- Raw URL: https://raw.githubusercontent.com/nam20485/agent-instructions/main/…/project-setup.md
- Canonical file: ai_instruction_modules/ai-workflow-assignments/dynamic-workflows/project-setup.md
```

It is **already present** in the image and in every cloned workspace (confirmed in the lima63 workspace
at `local_ai_instruction_modules/ai-dynamic-workflows.md`). The golden path uses it; lima63 did not.

---

## 4. The lima63 derailment — precise timeline

lima63 (`nam20485/gap-miner-v2-lima63`, webhook dispatch, opencode 1.15.13) stalled after 2 of 6
assignments. The de-noised transcript (`traces/gap-miner-v2-lima63-log.txt`, 1832→473 lines) shows the
exact derailment:

### Phase A — Discovery (succeeded, but via the long path)
| Log | Action |
|---|---|
| L379 | Agent reasons: *"look for the orchestrate-dynamic-workflow command in the `.opencode/commands/` directory"* |
| L380 | `→ Read .opencode/commands/orchestrate-dynamic-workflow.md` (the command file, **not** the local index) |
| L390 | `% WebFetch …/ai-workflow-assignments/orchestrate-dynamic-workflow.md` (remote) |
| L392 | `% WebFetch …/dynamic-workflows/project-setup.md` (remote) |
| L402 | Agent now **understands all 6 assignments + events** — discovery is complete |

**At this point discovery had succeeded** — the agent had fetched and comprehended `project-setup.md`.
It simply arrived there via command-file→remote instead of local-index→remote (more hops, more fragile,
but it worked).

### Phase B — Execution derailment (the real damage)
| Log | Action |
|---|---|
| L402–407 | Agent plans execution across 8 sequential-thinking steps |
| **L408** | **`✗ Skill "orchestrate-dynamic-workflow" failed`** |
| L409 | `Error: Skill "orchestrate-dynamic-workflow" not found. Available skills: …` |
| L414 | Agent: *"the orchestrate-dynamic-workflow skill is not available, I'll need to **manually orchestrate** the workflow"* |
| L446 | *"I should delegate to an orchestrator-type agent to manage the entire project-setup workflow execution"* |

**The skill error did not break discovery. It broke the execution strategy.** Faced with the error, the
agent abandoned *"execute the workflow definition I just fetched"* in favor of *"manually orchestrate via
subagent delegation."* That manual delegation path is what stalled — it completed `init-existing-repository`
and `create-app-plan`, then went silent before `create-project-structure`. Issue #1 was never closed.

### Phase C — The probable halt (post-capture)
The transcript capture ended `14:33:46`; artifacts continued to `14:41:30` (last commit) then stopped.
No `🏁 Dispatch complete` comment, no `orchestration:plan-approved` label, PR #2 unmerged, issue #1 open.
Most consistent explanation (see `traces/gap-miner-v2-lima63-run-report.md` §3): the stack was torn down
mid-run while the orchestrator was deep in manual multi-assignment delegation.

---

## 5. Would simply removing the skills fix it? (the key question)

**Short answer: partially, but not deterministically — and not for the reason it might seem.**

The skill error is **non-fatal** (the agent recovered at L414 and produced 2 correct assignments). Its
real cost was redirecting the agent onto the manual-orchestration path. So the question splits in two:

### 5a. Would removing the skills prevent the `Skill not found` error?
**Yes.** If no skill registry exists, opencode 1.15.x cannot route `/orchestrate-dynamic-workflow` to a
skill resolver, so no error is thrown. Removing the 8 skills (or disabling the skill system for the
orchestrator agent) eliminates the L408 error.

### 5b. Would that restore the golden-path behavior?
**Not by itself.** Two reasons:

1. **The discovery path would still be wrong.** lima63 reached `project-setup.md` via
   command-file→remote (L380→L392), **not** via the local index. Removing skills doesn't make the agent
   read `ai-dynamic-workflows.md` first. The golden path's reliability comes from the *local-index-first*
   read, which is independent of the skill system.

2. **The `/name` notation remains ambiguous.** Even without skills, opencode 1.15.x may still attempt to
   resolve `/orchestrate-dynamic-workflow` as a command (it IS a command in `.opencode/commands/`). That
   is closer to correct, but command invocation loads the command file's body (which points back to
   remote blob URLs) — still not the local-index path, and still a hop the golden path doesn't take.

3. **The golden path's success was partly version- and judgment-dependent.** foxtrot54 (1.2.24) read the
   local index from its *own reasoning* ("I need to look at the local instruction modules"), not from an
   explicit instruction. That judgment worked on 1.2.24 and failed on 1.15.13. Relying on judgment is
   exactly the fragility the user has observed across runs.

### 5c. Recommendation
- **Do not** rely on skill removal alone. It removes a symptom but leaves discovery non-deterministic.
- Removing/disabling the skills **is a reasonable complementary hardening** (fewer resolvable targets =
  less chance of misrouting `/name`), but it must be paired with the explicit local-index-first step
  (Tier 1) to be reliable.
- If skills are removed, confirm none of the 8 (`customize-opencode`, `plan-app`, `plan-to-beads`,
  `perfect-idea`, `validate-and-commit`, `forensic-analysis-report`, `orchestration-run-analysis`,
  `prompt-bisect`) are actually invoked elsewhere in the orchestrator's normal operation. Several look
  like developer/QA tooling, not runtime orchestration dependencies — candidates for removal.

---

## 6. Why `agent-instructions-expert` is not the fix

A dedicated `agent-instructions-expert` subagent (with a full topic→raw-URL knowledge map) was built and
tried specifically to improve discovery. **It did not outperform** the simple local-index→WebFetch path
that foxtrot54 uses. The proven mechanism is a **checked-in local index read as the first deterministic
step**, not a delegation subagent. Keep discovery inline and explicit. (This also avoids extra
subagent-session overhead and the single-writer memory coordination tax.)

---

## 7. Tiered change list

Each tier is independently shippable. Tier 1 is the high-leverage fix that closes the discovery
divergence; Tier 2 closes the "killed run leaves no trace" gap.

### Tier 1 — Core discovery alignment (minor, high leverage)

**Goal:** make the golden-path discovery+execution deterministic regardless of opencode version or model.

**T1.1 — Add an explicit discovery directive to the dispatch prompt**
- **File:** `webhook_receiver/orchestration_prompt.jinja2.md`
- **Change:** Add a mandatory discovery preamble that fires for every clause that invokes a dynamic
  workflow. Insert a `## Dynamic Workflow Discovery` helper section (alongside the existing
  `parse_workflow_dispatch_body` helper) and reference it from each `orchestration:*` clause:
  ```
  To execute `$workflow_name`:
  1. FIRST `read local_ai_instruction_modules/ai-dynamic-workflows.md` — it is the local registry
     of every dynamic workflow and contains each workflow's raw URL.
  2. From that index, take the raw URL for `$workflow_name` and WebFetch it → the workflow definition.
  3. WebFetch each assignment definition only as you reach it in the workflow script.
  4. Execute the assignments in order. Do NOT delegate the whole workflow to a single subagent;
     execute assignment-by-assignment (the golden-path pattern).
  ```
- **Why:** foxtrot54 read the local index from judgment; lima63 did not. Making it an explicit,
  numbered step removes the judgment dependency.
- **AC:** On the next dispatch, the de-noised run log shows
  `→ Read local_ai_instruction_modules/ai-dynamic-workflows.md` **before** any WebFetch or command-file read.

**T1.2 — Neutralize the `/name` skill/command collision**
- **File:** `webhook_receiver/orchestration_prompt.jinja2.md`
- **Change:** Reword every clause's invocation so it cannot be routed to the skill resolver. Replace
  `/orchestrate-dynamic-workflow $workflow_name = …` with explicit directive prose, e.g.:
  ```
  EXECUTE the dynamic workflow named "$workflow_name" (resolved via the Dynamic Workflow Discovery
  steps above). This is a directive to resolve-and-execute a workflow definition — it is NOT a
  slash-command, skill, or tool to invoke. Never call a skill/tool named "orchestrate-dynamic-workflow".
  ```
  Apply consistently across all clauses (`orchestration:plan-approved`, `epic-ready`, `epic-implemented`,
  `epic-reviewed`, `epic-complete`, `orchestration:dispatch`).
- **Why:** the inherited Copilot-prompt `/name` notation collides with opencode 1.15.x skill resolution.
  This is the version-behavior-change that derailed lima63 (and would not have derailed 1.2.24).
- **AC:** On the next dispatch, the run log shows **no** `Skill "orchestrate-dynamic-workflow" … not found`
  line, and the agent proceeds straight from the local index to execution.

**T1.3 — Verify the local index is current with `main`** (read-only check)
- **File:** `image/local_ai_instruction_modules/ai-dynamic-workflows.md`
- **Change:** Diff against `nam20485/agent-instructions@main` workflow list; add any missing workflows
  (e.g. confirm `project-setup`, `create-epic-v2`, `implement-epic`, `review-epic-prs`, `single-workflow`
  are all listed). No branch change — it already declares `Branch: main`, which matches our decision.
- **AC:** Every dynamic workflow referenced by the prompt's clauses has an entry in the index.

### Tier 2 — Run robustness (moderate)

**Goal:** a torn-down or failing run always leaves a diagnosable trace and a terminal issue state.

**T2.1 — Runner failure comment** (port the golden-path `post-failure-comment` step)
- **Files:** `webhook_receiver/runner.py`; optionally `scripts/post-failure-comment.sh` (mirror the
  golden-path `intel-agency/…/scripts/post-failure-comment.sh`).
- **Change:** When the `opencode run` subprocess exits non-zero (or is killed, or times out), post a
  failure comment on the triggering issue: `❌ Orchestrator run did not complete (exit/status …). See
  runner logs at <host path>.` The golden-path GHA does exactly this with a `if: failure()` step; the
  webhook runner currently has no equivalent, which is why lima63's kill left issue #1 dangling silently.
- **AC:** A killed/failed run posts a comment on the dispatch issue within the timeout window.

**T2.2 — Persist runner logs to the host**
- **Files:** `compose.yaml`, `compose.development.yaml` (webhook-receiver `volumes:`).
- **Change:** Add a bind mount so the ephemeral runner log dir survives container teardown:
  ```yaml
  volumes:
    - ${WORKSPACE_DIR:?WORKSPACE_DIR is required}:/workspace
    - ${WEBHOOK_LOG_DIR:-./traces/runner}:/tmp/orchestrator-webhook
  ```
  (`runner.py` and `beads_loop.py` both write to `Path(tempfile.gettempdir())/"orchestrator-webhook"`.)
- **Why:** lima63's `prompt-v7qzak74.{stdout,stderr}` were lost — no containers remained and the dir was
  not mounted. This is the only way to diagnose a post-capture halt without guessing. Bonus: the in-repo
  dashboard (`dashboard.py`) already reads `bead-*` from this same dir, so persistence also improves
  cross-restart observability.
- **AC:** After `compose down`, runner `stdout`/`stderr` for the last run are still readable on the host.

**T2.3 — Enforce a terminal dispatch state** (prompt + runner)
- **Files:** `orchestration_prompt.jinja2.md` (already has close-on-success / leave-open-on-failure
  logic — verify it is reached); `runner.py` (T2.1 covers the failure path).
- **Change:** Confirm the `orchestration:dispatch` clause's success branch (push → PR → `🏁 Dispatch
  complete` → close) is the literal last action, and that T2.1 covers the failure/kill branch. A run must
  never end with the issue silently open and no comment.
- **AC:** Every dispatch ends in either (a) issue closed with a success comment, or (b) issue open with
  a failure/timeout comment.

### Tier 3 — Consistency & observability (minor, optional)

**T3.1 — Correct the root `AGENTS.md` branch value**
- **File:** `AGENTS.md` (root), line ~233.
- **Change:** `Branch: optimization` → `Branch: main` (and the `{branch}` placeholders it drives).
- **Why:** The runtime files all say `main`; the root doc says `optimization`. Since we've decided to
  stay on `main`, this is a consistency fix to avoid misleading future readers. (The runtime was
  unaffected — the image/local-index/commands all already say `main`.)
- **AC:** No occurrence of `optimization` as the configured agent-instructions branch in any repo file.

**T3.2 — Per-assignment status comments** (optional, lower priority)
- **Change:** Have the agent `postStatusUpdate` at each assignment boundary so progress is visible in the
  issue thread (not only in container logs). This mirrors how the golden-path run is observable in the
  GHA run log.
- **AC:** A multi-assignment dispatch produces one status comment per assignment.

**T3.3 — Consider pruning the skill registry** (optional, complements T1.2)
- **Change:** Audit the 8 available skills; remove any that are pure developer/QA tooling and not
  runtime orchestration dependencies. Fewer resolvable `/name` targets reduces misrouting risk.
- **AC:** Skill list contains only skills the orchestrator actually needs at runtime.

---

## 8. Risk & sequencing

- **Tier 1 is the high-leverage, low-risk change** — pure prompt edits to files we control
  (`orchestration_prompt.jinja2.md`) + a read-only index check. It directly closes the divergence that
  made lima63 behave differently from foxtrot54. Ship this first; validate on one dispatch before Tier 2.
- **Tier 2 touches runtime/compose** (`runner.py`, compose files) — slightly higher risk, needs the
  validation suite (`./scripts/validate.ps1 -All`) plus a real dispatch test. Ship after Tier 1 is green.
- **Tier 3 is cosmetic/optional.** T3.1 (the 1-line branch fix) is trivial and can ride any commit.
- **Do not** attempt the `optimization` branch cutover (out of scope; see §1 decision).

### What this does NOT change
- The branching-logic clauses themselves (unchanged — they are correct and match the golden path).
- The `main` branch of `agent-instructions` (the source of truth; unchanged).
- The memory single-writer protocol, the label set, or the match-clause semantics.

---

## 9. Validation plan

1. **After Tier 1:** trigger a fresh `orchestration:dispatch` → `project-setup` against a test repo.
   De-noise the run log and assert:
   - `→ Read local_ai_instruction_modules/ai-dynamic-workflows.md` appears **before** any WebFetch.
   - **No** `Skill "orchestrate-dynamic-workflow" … not found` line.
   - The agent executes assignment-by-assignment (not a single "manual orchestrate" delegation).
2. **After Tier 2:** kill the stack mid-run; assert the dispatch issue gets a failure/timeout comment and
   the runner logs survive on the host.
3. **Local validation gate:** `pwsh -NoProfile -File ./scripts/validate.ps1 -All` clean before any commit
   (lint + scan + test), per repo convention.
4. **Reference comparison:** the de-noised log should now resemble foxtrot54's `0_orchestrate.txt`
   discovery sequence (local index → targeted WebFetch → in-order execution).
