# Run Report — gap-miner-v2-golf38 (API Error + Deadlock)

**Repo:** `nam20485/gap-miner-v2-golf38`
**Run ID:** `1a0fabcb`
**Trigger:** `issues / labeled` — label `gh-issue-tracking:direct-body` on issue #1
**Issue body (verbatim prompt):** `/gh-issue-tracking-init`
**Window:** 2026-07-23 03:40:08 → 04:08:08 (killed, ~28 min)
**Outcome:** ❌ **INCOMPLETE — aborted by user.** No plan/epic/story issues, no project board, no milestones were created. Only 2 status comments were posted on issue #1 (still OPEN).

---

## TL;DR — Two failure modes, one fatal

| # | Failure | When | Recovered? | Fatal? |
|---|---------|------|------------|--------|
| 1 | `AI_APICallError: service temporarily overloaded` (glm-5.2) | 03:43:31–03:43:52 (orchestrator, ×3) + 03:58:26 (developer, ×1) | Yes — opencode auto-retried all 4 | No |
| 2 | **Unanswerable `external_directory` permission prompt** for `/tmp/kilo/gap-miner-v2-golf38/*` | 03:58:34 (developer subagent) | **No** — headless run, no one to answer `ask` | **YES** |

The API error the user spotted (03:58:26 on the developer subagent) was **transient and already auto-retried**. The *actual* reason the run stalled was a **permission deadlock** 8 seconds later: the developer subagent tried to write scratch files under `/tmp/kilo/gap-miner-v2-golf38/*`, which fired an `external_directory` **ask** no human can approve in a headless container.

### ⚠ CORRECTION — why the two `/tmp/**` allow fixes did NOT prevent this
Two fixes were already deployed and **verified baked into the running image** (`nam20485-latest`, built 2026-07-23 03:23 UTC — 17 min before the run):
- Top-level `opencode.json` → `permission.external_directory: {"*":"deny","/tmp/**":"allow"}`
- Per-agent `image/.opencode/agents/developer.md` frontmatter → `permission.external_directory: {"*":"deny","/tmp/**":"allow"}`

Both are **inert for task-spawned subagents in opencode v1.18.4** — an opencode bug, not a config/glob problem:
1. `deriveSubagentSessionPermission` (v1.18.4) forwards `external_directory` rules **only from the parent SESSION**; the orchestrator session is empty under `--dangerously-skip-permissions`, so the child inherits none.
2. `session/prompt.ts:1065` does `session.permission = permissions` (**replace, not merge**), clobbering the rules the subagent derivation placed — leaving only per-call tool denies. This is opencode issue **#30527**, in the cluster with #12566, #20864, #27497, #28052 (subagent permission inheritance broken / not transitive in headless mode).
3. The built-in agent `defaults` already whitelist `os.tmpdir()` (`/tmp/*`) for every agent, yet the subagent still got `ask` — proving the subagent's effective `external_directory` ruleset was empty and only the default catch-all (`external_directory: * → ask`) fired.

---

## Sessions

| Role | Session ID | Model | Steps reached |
|------|-----------|-------|---------------|
| Orchestrator (primary) | `ses_072f0d1e2ffeeqQfvhIg3iAPjf` | zai-coding-plan / glm-5.2 | step 13 (blocked, waiting on subagent) |
| Developer (subagent) | `ses_072e9872affe81C64X3RZYVuoW` | zai-coding-plan / glm-5.2 | **step 21 → deadlocked** |

---

## Timeline (annotated)

| Time (UTC) | Event |
|------------|-------|
| 03:40:06 | Webhook received: `issues / labeled`, label `gh-issue-tracking:direct-body`, issue #1 |
| 03:40:08 | Orchestration run started; watchdog armed (idle 1800s, ceiling 5400s) |
| 03:40:10 | Orchestrator session created. ⚠ Title-gen stream hit `AI_LoadAPIKeyError` for `google/gemini-3.5-flash` (dead small_model config) — non-fatal, swallowed. |
| 03:41:03–03:43:30 | Orchestrator: memory load (empty), read repo + plan + SKILL.md + scripts, pre-parsed full hierarchy (**1 Plan + 7 Epics + 22 Stories = 30 issues**), milestones (3 gates), dependencies. |
| 03:43:31–03:43:52 | **3× transient API overload** on orchestrator — all auto-retried, recovered. |
| 03:45:52 | Status update #1 posted on issue #1 (comment 5054075644). |
| 03:45:53 | Best-effort project discovery: 5 projects exist but **none for golf38** → link skipped silently (correct; skill creates it). |
| 03:46:24 | Status update #2 posted (comment 5054078757): "Delegating to the `developer` specialist…" |
| 03:48:08 | **Developer subagent spawned** (`ses_072e9872affe81C64X3RZYVuoW`). |
| 03:48:08–03:51:25 | Developer steps 0–20: read SKILL.md, scripts/README.md, all 4 templates, labels.json, plan_docs/development-plan.md, common.ps1 + all 8 op scripts; ran env checks (`pwsh -v` ✓, `gh auth` ✓, confirmed **no existing project/issues/milestones**). |
| 03:51:25 | Developer step 20 — last successful server activity before composing the driver script. |
| 03:51:38–03:58:08 | **~7 min stall** (watchdog: `line_idle` climbing to 600s, `server_log_idle` to 390s). Subagent was generating the large driver script / body files (or silently retrying throttled requests). |
| 03:58:24 | Developer step 21 begins. |
| **03:58:26** | **`AI_APICallError: service temporarily overloaded`** on developer subagent (the error the user flagged). Auto-retried at 03:58:28. |
| **03:58:34** | **DEADLOCK**: developer resolved `/tmp/kilo/gap-miner-v2-golf38/bodies` + `/diag`, fired `external_directory` **ask** (`per_f8d2006c9001jyrpKgZSIkrCsS`) for `/tmp/kilo/gap-miner-v2-golf38/*`. No answer possible in headless mode → run frozen. |
| 03:58:38–04:08:08 | Watchdog ticked every 30s; `server_log_idle` climbed back to 570s. The server was parked on the unanswered permission request. Watchdog **never tripped** (idle ceiling 1800s; user killed at 1680s before it fired). |
| 04:08:08 | User hit `^C` and ran `docker compose down`. |

---

## Root cause analysis

### The API error was a symptom, not the cause
The `AI_APICallError: The service may be temporarily overloaded` is a **transient upstream (zai-coding-plan / glm-5.2) capacity error**. opencode's built-in retry recovered every occurrence:
- Orchestrator: 3 errors at 03:43:31/35/52 → all recovered by 03:43:54.
- Developer: 1 error at 03:58:26 → retried successfully at 03:58:28 (the model *did* respond, which is why the `external_directory` write attempt followed at 03:58:34).

So the run did **not** die on the API error. It died on what the model tried to do *after* recovering from it.

### The real killer: external_directory permission deadlock
The orchestrator's delegation prompt told the developer to:
> "compose ONE driver.ps1 under `/tmp/kilo/gap-miner-v2-golf38/`, init logfile first, render 30 bodies…"

When the developer tried to materialize those scratch artifacts (`/tmp/kilo/gap-miner-v2-golf38/bodies`, `/diag`), the server evaluated the path against the subagent's permission set and found **no matching allow rule** → action `ask`. In a headless fire-and-forget container run there is no interactive session to grant `ask`, so the request blocks forever.

This matches the documented `headless_orchestration_permission_deadlock` correction exactly:
> Fire-and-forget runs deadlock when a subagent hits an unanswerable permission `ask`. PRECISE mechanism: subagent `external_directory` is NOT inherited from global opencode config.

The `/tmp/kilo/*` path is **not** in the subagent's inherited allow-list, so writing the driver script + body files there triggers an unanswerable prompt.

---

## Run progress vs. completion state

**What got done (orchestration side — good):**
- ✅ Correctly matched the `gh-issue-tracking:direct-body` clause.
- ✅ Posted 2 status comments on issue #1.
- ✅ Best-effort project link correctly skipped (no project exists yet).
- ✅ Pre-parsed the entire hierarchy accurately (30 nodes, 3 milestone gates, dependency graph).
- ✅ Dispatched a well-structured, self-contained delegation prompt to a `developer` subagent.

**What the developer subagent did (good):**
- ✅ Read the full SKILL.md spec + scripts README + all 8 op scripts + all 4 body templates + labels.json + the development plan.
- ✅ Verified the environment: `pwsh` present, `gh` authenticated (project scope), no existing project/issues/milestones (clean repo — safe to init).

**What did NOT happen (the skill's actual deliverables — none reached):**
- ❌ Driver script never composed (or never persisted — blocked at the write).
- ❌ 0 of 30 issue bodies rendered.
- ❌ DryRun assertions never run.
- ❌ **No issues created** (GitHub confirmed: only issue #1 exists, OPEN).
- ❌ **No project board created** (no golf38 project exists).
- ❌ **No milestones created** (none exist).
- ❌ No labels ensured, no sub-issue links, no board fields, no dependencies, no `gh-issue-tracking:init-success` label.
- ❌ Issue #1 never closed; orchestrator never regained control (still parked at step 13 waiting on the deadlocked subagent).

**Net effect on the repo:** zero mutations except 2 comments on issue #1. A re-run of `/gh-issue-tracking-init` is safe (the skill is idempotent and the repo is still clean).

---

## Why the watchdog didn't save it
The watchdog's `server_log_idle` signal is the mtime of the opencode server log. During the 7-minute generation stall (03:51–03:58) the server was technically alive (just slow / waiting on the LLM), and after the deadlock at 03:58:34 it briefly logged the permission-ask lines (resetting idle to 0s) before going silent again. The idle ceiling (1800s) was ~2 min away from tripping when the user killed the container at 1680s. **The watchdog would eventually have killed it**, but only after another ~2 min of nothing.

---

## Recommendations

1. **Root cause = opencode v1.18.4 subagent-permission inheritance bug, NOT config.** Granting `/tmp/**` at config/agent-frontmatter level is confirmed inert (both were already deployed & baked into the image). Recommended fixes, strongest first:
   - **(A) Avoid `external_directory` entirely — write scratch inside the workspace.** Change the orchestrator delegation prompt / `gh-issue-tracking-init` SKILL guidance (and any "use `/tmp/kilo`" instruction) to write driver scripts + body files **under the project workspace** (`/workspace/<project>/.scratch/` or the opencode data dir). Workspace writes are never "external," so `external_directory` is never consulted. 100% robust, version-independent.
   - **(B) Upgrade opencode past the bug.** The clobber at `prompt.ts:1065` (issue #30527, fix = merge instead of replace) and the non-transitive inheritance (#27497/#28052) are fixed in post-1.18.4 releases. Bump `OPENCODE_VERSION` in the Dockerfile once a stable release containing #30527 is confirmed. Verify the V1→V2 permission migration didn't introduce new headless quirks.
   - **(C) Config-level deny-fail-fast (partial).** `"permission": {"*": {"*": "allow"}, "external_directory": {"*":"deny"}}` converts a hang into an immediate tool failure the agent can react to — but the deny may also block legitimate workspace-adjacent reads; test carefully.

2. **Stop conflating the symptom.** The glm-5.2 overload errors are transient and self-healing; they are **not** what stalls these runs. Logging/filtering should not treat them as run-ending.

3. **Watchdog: add a permission-ask detector (defense-in-depth for any `ask` deadlock).** A pending `ask` (`message=asking ...`) with no resolution is a deterministic deadlock signature — short-circuit and kill immediately (or fail the run with a clear cause) instead of waiting for the generic idle ceiling.

4. **Re-run is safe.** Re-trigger `/gh-issue-tracking-init` on issue #1 after applying fix (1). The repo is clean (only issue #1 + 2 comments exist; no partial hierarchy).
