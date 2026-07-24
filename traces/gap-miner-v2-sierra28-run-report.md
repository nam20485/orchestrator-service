# Run Report — gap-miner-v2-sierra28 (Permission Deadlock)

**Repo:** `nam20485/gap-miner-v2-sierra28`
**Run ID:** `0d9c5e43`
**Session:** `ses_06a1cc8e2ffe5405rml166KTD4` (orchestrator) + `ses_06a19764bffeo5gF2vVfcw0mAr` (developer subagent)
**Trigger:** `issues / opened` then `issues / labeled` — label `gh-issue-tracking:direct-body` on issue #1
**Issue body (verbatim prompt):** `/gh-issue-tracking-init`
**Window:** 2026-07-24 20:48:40 → 20:57:42 UTC (~9 min, 540s) — ended by **watchdog abort** (status 200)
**Outcome:** ❌ **ABORTED BY WATCHDOG.** No plan/epic/story issues, no project board, no milestones created. 1 status comment posted on issue #1 (still OPEN).

---

## TL;DR — one failure mode, fatal

| # | Failure | When | Recovered? | Fatal? |
|---|---------|------|------------|--------|
| 1 | **Unanswerable `external_directory` permission prompt** for `/tmp/kilo/gap-miner-v2-sierra28/*` (developer subagent) | 20:56:23 | **No** — headless run, no one to answer `ask` | **YES** |

This run was a **clean deadlock with zero API errors** (no glm-5.2 overload, no dead small_model). The developer subagent reached step 10, then tried to write scratch files under `/tmp/kilo/gap-miner-v2-sierra28/*`. Because task-spawned subagents in opencode v1.18.4 inherit **no** `external_directory` allow rules, the write fell to the default catch-all `external_directory → ask`, which is unanswerable in a headless container. **This is a recurrence of the exact same root cause documented in the golf38 report** (`headless_orchestration_permission_deadlock`).

### ✅ What went RIGHT this time (vs golf38)
The watchdog's **new permission-deadlock detector** (golf38 recommendation #3, since implemented) **worked**: it detected the unanswered `ask` and terminated the run cleanly at `ask_age=60s` / `elapsed=540s`, instead of hanging until a human killed it (as happened in golf38). **The watchdog abort was CORRECT behavior** — it protected against an indefinite hang. The cause is the upstream unanswerable prompt, not the kill.

### ⚠ Unrelated: model-tier override (accurate for this run; now solved for future clones)
The `developer` subagent streamed **`glm-5.2`**, NOT the intended flat-tier **`glm-5`** that the orchestrator streamed. **Verified accurate for this run** — sierra28's cloned `developer.md` frontmatter said `model: zai-coding-plan/glm-5.2`. It did **not** cause the deadlock. **Status: solved for future clones.** Fix `workflow-launch2 e5352c7` added `apply-glm5-models.ps1` (a post-clone rewrite of the loaded project-layer `model:` → `glm-5`, wired into `create-repo-agent-context.ps1`). It did not reach sierra28 only because sierra28 was cloned before the script existed.

### 🔑 POST-VERIFICATION: why the existing scratch fix did NOT prevent this run
A scratch-in-workspace fix already existed — commit `599ac12` added a "Subagent Scratch Location (CRITICAL)" rule. **It was applied to the wrong layer and was inert for this run.** The rule went into `image/.opencode/agents/orchestrator.md`, which `Dockerfile:116-128` installs to the **global** config dir (`/home/app/.config/opencode/`). opencode resolves agents with **project-overrides-global** precedence, so the cloned project's `.opencode/agents/orchestrator.md` (sourced from the `intel-agency/agent-context` template) **shadows the global one by name** — and the template's version had **no scratch rule**. The orchestrator that actually ran therefore never saw the in-workspace mandate and delegated `/tmp/kilo/<slug>/`. The same shadowing is why the global `"permission": "allow"` (claimed in `AGENTS.md:29` as a "DEFINITIVE FIX") never reached the subagent — the project config's `"permission": {"websearch":"allow"}` and the inheritance defect override it (matches correction `headless_orchestration_permission_deadlock_corrected`). **Fix applied this session:** the scratch rule was added to the *template* `intel-agency/agent-context/.opencode/agents/orchestrator.md` (the layer that is actually loaded).

---

## Sessions

| Role | Session ID | Model (streamed) | Steps reached |
|------|-----------|------------------|---------------|
| Orchestrator (primary) | `ses_06a1cc8e2ffe5405rml166KTD4` | `zai-coding-plan / glm-5` | step 13 (parked, waiting on subagent) |
| Developer (subagent) | `ses_06a19764bffeo5gF2vVfcw0mAr` | `zai-coding-plan / glm-5.2` ⚠ | **step 10 → deadlocked** |

---

## Definitive root cause (with evidence)

**The developer subagent attempted an `external_directory` write to `/tmp/kilo/gap-miner-v2-sierra28/*`, and opencode v1.18.4 supplied it no allow rule, so the default catch-all fired `ask` — unanswerable in headless mode.**

Exact log lines:

```
orchestratorservice | 2026-07-24T20:56:17.083Z run=0d9c5e43 message=loop session.id=ses_06a19764bffeo5gF2vVfcw0mAr step=10
orchestratorservice | 2026-07-24T20:56:23.310Z run=0d9c5e43 message=asking id=per_f95ea3a0e001vO1S1ykizxD3QE permission=external_directory patterns=["/tmp/kilo/gap-miner-v2-sierra28/*"]
   # (no resolution ever follows — the ask hangs)
orchestratorservice | 2026-07-24T20:57:42.153Z run=0d9c5e43 level=ERROR message=process session.id=ses_06a19764bffeo5gF2vVfcw0mAr ... error=Aborted
webhook-receiver     | 2026-07-24 20:57:42,123 WARNING [watchdog] PERMISSION DEADLOCK unanswered ask ask_age=60s grace=60s detail='message=asking id=per_f95ea3a0e001vO1S1ykizxD3QE permission=external_directory' elapsed=540s lines=491 — terminating
webhook-receiver     | 2026-07-24 20:57:42,468 WARNING [watchdog] server session aborted url=http://orchestratorservice:4099/session/ses_06a1cc8e2ffe5405rml166KTD4/abort status=200
```

**Why no allow rule was in effect (config provenance — all inert for the subagent):**

1. **Global config** `/home/app/.config/opencode/opencode.json` (via `docker compose exec cat`):
   `"permission": "allow"` (string blanket) — but task-spawned subagents do NOT inherit this.
2. **Project config** `/workspace/nam20485-gap-miner-v2-sierra28/.opencode/opencode.jsonc`:
   `"permission": { "websearch": "allow" }` — a **narrowing object** that further restricts (no `external_directory` entry at all); and it overrides `"model": "zai-coding-plan/glm-5.2"` over the global `glm-5`.
3. **Agent frontmatter** `/workspace/nam20485-gap-miner-v2-sierra28/.opencode/agents/developer.md`:
   ```yaml
   model: zai-coding-plan/glm-5.2          # overrides global glm-5 ⚠
   permission:
     external_directory: ask               # inert for task-spawned subagents
   ```
   Declaring `external_directory: ask` (or even `allow`) is **inert** for a task-spawned subagent in v1.18.4 — `session.permission` is assigned by *replace*, not *merge*, and `external_directory` rules are not inherited from config/frontmatter into the subagent session. The session that was created carried only the dispatch CLI's per-call denies:
   ```
   permission=[{"permission":"question","pattern":"*","action":"deny"},
               {"permission":"plan_enter","pattern":"*","action":"deny"},
               {"permission":"plan_exit","pattern":"*","action":"deny"}]
   ```
   No `external_directory` allow rule exists → the write to `/tmp/kilo/*` matched the **default catch-all `external_directory → ask`** → unanswerable prompt.

This matches the documented corrections verbatim:
> `headless_orchestration_permission_deadlock` — Fire-and-forget runs deadlock when a subagent hits an unanswerable permission `ask`. PRECISE mechanism: subagent `external_directory` is NOT inherited from global opencode config.
> `external_directory_glob_nesting` — not a glob problem; the subagent's effective `external_directory` ruleset is empty.

**Conclusion:** the watchdog abort was correct behavior. The single upstream cause is the **opencode v1.18.4 subagent-permission inheritance defect** — the same root cause as golf38, recurring on sierra28 because the upstream fix was never applied (only the watchdog mitigation was).

---

## Solutions (options, recommendation, why)

**(A) Avoid `external_directory` entirely — write scratch inside the workspace.** ✅ **Applied this session to the template** (`intel-agency/agent-context/.opencode/agents/orchestrator.md`), the layer opencode actually loads. This supersedes the earlier `599ac12` edit which correctly authored the rule but put it in `image/.opencode/` (shadowed by the cloned project agents — see POST-VERIFICATION above). Future clones now carry the in-workspace mandate. Workspace writes are never "external," so `external_directory` is never consulted — robust and version-independent.

**(B) Upgrade opencode past the inheritance bug.** Still open. The clobber (`session.permission = permissions`, replace-not-merge) and the non-transitive `external_directory` inheritance are fixed in post-1.18.4 releases. Bump `OPENCODE_VERSION` once a stable release containing the fix is confirmed. This is the durable fix for *any* future out-of-workspace write; (A) makes it non-urgent.

**(C) Model override → glm-5.** ✅ **Already solved** by `workflow-launch2 e5352c7` (`apply-glm5-models.ps1`, a post-clone rewrite of the loaded project layer). Redundant to re-apply. (The earlier `4c0595e` set the global default to `glm-5`, but global/agent-frontmatter model is itself defeated by the cloned project layer — the post-clone script is the correct layer to touch.)

**(D) Harden template specialist agents: `external_directory: ask` → `deny` (secondary, defense-in-depth).** Template `ff3baf8` flipped coordinators to `deny` but left specialists (`developer`, `code-reviewer`, `researcher`, `qa-tester`) on `external_directory: ask` / `webfetch: ask` / `websearch: ask`. These are currently inert (inheritance bug) and moot once (A) is in effect, but an out-of-workspace write by a specialist would still hang rather than fail fast. Flip to `deny` for consistency with the coordinator deny-rule rationale.

**RECOMMENDATION: (A) is done; re-run sierra28 to confirm.** Then (D) as cheap hardening. (B) when a stable opencode release lands. (C) already covered. Note: sierra28 is a stale clone (predates both fixes) — re-create the repo (or re-clone) so the template scratch rule + `apply-glm5-models.ps1` reach it; a plain re-trigger on the existing clone will still carry the old `orchestrator.md`.

---

## Timeline (annotated, UTC)

| Time (UTC) | Event |
|------------|-------|
| 20:48:22 | Webhook: `installation_repositories` (repo added) — benign setup event. |
| 20:48:40 | Webhook: `issues / opened`, issue #1 on `gap-miner-v2-sierra28`, body `/gh-issue-tracking-init`. |
| 20:48:41 | Webhook: `issues / labeled`, label `gh-issue-tracking:direct-body` → matched the direct-body clause. |
| 20:48:43 | Orchestrator session created `ses_06a1cc8e2ffe5405rml166KTD4`. Watchdog armed. |
| 20:48:44–20:51:29 | Orchestrator steps 0–12: memory load, read repo + plan + SKILL.md + scripts, matched clause, planned delegation. |
| ~20:49 | Orchestrator posted initial status comment on issue #1 (comment 5074288029). |
| 20:52:21 | **Developer subagent spawned** `ses_06a19764bffeo5gF2vVfcw0mAr` (parent=orchestrator). Streamed **`glm-5.2`** ⚠. |
| 20:52:21–20:56:17 | Developer steps 0–10: read SKILL.md + scripts + templates + labels + development-plan; env checks (`gh auth` ✓); **pre-parsed plan → "6 Phases (Phase 0–6), 17 tasks (T-0.1–T-6.3)"**. |
| 20:56:17 | Developer step 10 — last successful server activity before composing scratch artifacts. |
| **20:56:23** | **DEADLOCK**: developer attempted `/tmp/kilo/gap-miner-v2-sierra28/*` write → `asking id=per_f95ea3a0... permission=external_directory`. No answer possible in headless mode → frozen. |
| 20:56:42–20:57:12 | Watchdog ticks; `server_log_idle` climbing; subagent parked on the unanswered ask. |
| **20:57:42** | **Watchdog: `PERMISSION DEADLOCK unanswered ask ask_age=60s grace=60s elapsed=540s` → terminating.** Developer task cancelled; both sessions aborted (`status=200`). |

---

## Run progress vs. completion state

**What got done (orchestration side — good):**
- ✅ Correctly matched the `gh-issue-tracking:direct-body` clause.
- ✅ Posted 1 status comment on issue #1 (5074288029).
- ✅ Best-effort project link correctly deferred (no project exists yet).
- ✅ Dispatched a self-contained delegation prompt to a `developer` subagent.

**What the developer subagent did (good):**
- ✅ Read the full SKILL.md spec + scripts README + templates + labels.json + the development plan.
- ✅ Verified environment (`gh` authenticated, clean repo — safe to init).
- ✅ Pre-parsed the hierarchy accurately (6 Phases, 17 tasks) before the write.

**What did NOT happen (the skill's deliverables — none reached):**
- ❌ Driver script never persisted (blocked at the `/tmp/kilo/*` write).
- ❌ 0 issue bodies rendered; DryRun assertions never run.
- ❌ **No issues created** (only issue #1 exists, OPEN).
- ❌ **No project board / milestones / labels / links created.**
- ❌ Issue #1 never closed; orchestrator never regained control (parked at step 13).

**Net effect on the repo:** zero mutations except 1 comment on issue #1. A re-run of `/gh-issue-tracking-init` is safe (the skill is idempotent and the repo is still clean).

---

## Why the deadlock recurred (status of the golf38 fixes)

| golf38 recommendation | Status | Effect on sierra28 |
|----------------------|--------|--------------------|
| (3) Watchdog: add a permission-ask detector | ✅ **Implemented & worked** | Run killed cleanly in 60s instead of hanging to user ^C. |
| (A) Write scratch inside workspace (avoid `external_directory`) | ⚠ **Applied to `image/.opencode/` (599ac12) but SHADOWED** — opencode loads the cloned project agents (template), which override the global image agents by name. Template lacked the rule. **Fixed this session** by adding it to the template. | sierra28 clone predated the template fix → developer wrote to `/tmp/kilo/<slug>/` → deadlock. |
| (B) Upgrade opencode past the inheritance bug | ❌ **Not applied** (still v1.18.4) | Subagent `external_directory` still not inherited. |
| (C) Model override → glm-5 | ✅ **Solved** post-clone (`apply-glm5-models.ps1`, workflow-launch2 `e5352c7`) | Did not reach sierra28 (stale clone) → developer streamed `glm-5.2`. Not the cause of the deadlock. |

**Root cause of the recurrence:** fixes were authored in `image/.opencode/` (the global layer), but opencode's **project-overrides-global** agent resolution makes the cloned `agent-context` template the decisive layer. The watchdog mitigation landed; the behavioral fixes did not reach the loaded layer until this session. **Re-create/re-clone sierra28** so the template scratch rule + glm-5 normalization apply; a re-trigger on the stale clone will reproduce the deadlock.

---

## Evidence sources

- `docker compose -f compose.yaml ps` — stack up, 3 services healthy.
- `docker compose logs --since=60m orchestratorservice webhook-receiver | grep …` — session creation, step progression, the `asking`/`Aborted` lines, model stream lines.
- `docker compose logs --since=12m webhook-receiver | grep watchdog|runner` — watchdog idle ticks, `PERMISSION DEADLOCK` + pre-termination diagnostics (todos), abort `status=200`.
- `docker compose exec orchestratorservice cat /home/app/.config/opencode/opencode.json` — global: `permission: "allow"`, `model: glm-5`.
- `docker compose exec orchestratorservice cat /workspace/nam20485-gap-miner-v2-sierra28/.opencode/opencode.jsonc` — project: `permission: { "websearch": "allow" }`, `model: glm-5.2` (override).
- `docker compose exec orchestratorservice cat …/agents/developer.md` — frontmatter `model: glm-5.2`, `external_directory: ask`.
- `docker compose exec orchestratorservice cat …/agents/orchestrator.md` — frontmatter `model: opencode-go/qwen3.7-max` (overridden by dispatch `--model glm-5`), `edit: deny`, `bash "*": deny`.
- Session-creation log line (orchestrator + developer) — effective `permission=[…3 denies only…]`, confirming no inherited `external_directory` allow rule.
- Prior report `traces/gap-miner-v2-golf38-run-report.md` and corrections (`headless_orchestration_permission_deadlock`, `external_directory_glob_nesting`, `glm5_high_flat_tier_decision`).
