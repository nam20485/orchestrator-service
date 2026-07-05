# Golden Path — `project-setup` Dynamic Workflow (foxtrot54)

> **Purpose:** This document records a reference "perfect run" of the **`project-setup`** dynamic
> workflow so it can be studied and replicated (e.g. for
> [`nam20485/gap-miner-v2-delta48`](https://github.com/nam20485/gap-miner-v2-delta48), whose run
> stalled — see `traces/gap-miner-v2-delta48-execution-flow.md`).
>
> The **verbatim job log is copied alongside this file** at
> [`traces/golden-path-foxtrot54.log.txt`](./golden-path-foxtrot54.log.txt) (1,771 lines).

## Source of truth (real repo)

| Item | Value |
|------|-------|
| Repository | [`intel-agency/workflow-orchestration-queue-foxtrot54`](https://github.com/intel-agency/workflow-orchestration-queue-foxtrot54) |
| Actions run | `24971284567` — <https://github.com/intel-agency/workflow-orchestration-queue-foxtrot54/actions/runs/24971284567> |
| Job (this log) | `73114674392` — <https://github.com/intel-agency/workflow-orchestration-queue-foxtrot54/actions/runs/24971284567/job/73114674392> |
| Triggering event | `workflow_run` — the **"Pre-build dev container image"** workflow completing `success` on `main` (run `24971266457`) |
| Date | 2026-04-27, 00:43:09 → 02:13:49 UTC |
| Orchestrator | `opencode run` — model **`zai-coding-plan/glm-5`**, agent **`orchestrator`**, attached to the devcontainer's opencode server on `:4096` |
| Outcome | **Exit code 0** — full `project-setup` script executed end-to-end |

## Why this is the "golden path"

This is the one observed run where the orchestrator agent mechanically walked the entire
`project-setup.md` script — every assignment **and** every inter-assignment event handler — to
completion in a single ~90-minute session, exiting cleanly. It is the behavior we are trying to
reproduce on every fresh repo.

The expected `project-setup` script (from
[`agent-instructions/.../dynamic-workflows/project-setup.md`](https://github.com/nam20485/agent-instructions/blob/main/ai_instruction_modules/ai-workflow-assignments/dynamic-workflows/project-setup.md)) is:

```
pre-script-begin : create-workflow-plan

for each assignment in [ init-existing-repository,
                         create-app-plan,
                         create-project-structure,
                         create-agents-md-file,
                         debrief-and-document,
                         pr-approval-and-merge ]:
    run assignment
    post-assignment-complete : validate-assignment-completion
    post-assignment-complete : report-progress

post-script-complete : apply label orchestration:plan-approved to the plan issue
```

That is **1 pre-event + 6 main assignments × (1 assignment + 2 POC events) + 1 post-event**
= roughly 19 dispatched units, all in sequence with the orchestrator reviewing and approving each.

## Execution timeline (UTC, 2026-04-27)

All times are job-log timestamps. Tick `✅` = the orchestrator printed its checklist with that line
marked `[x]` (re-printed after every step, which is how progress is verified in the log).

| Offset | Time | Event | ✅ |
|--------|------|-------|----|
| +0:00 | 00:43:09 | Job starts; pulls prebuilt devcontainer image | |
| +0:38 | 00:43:47 | Orchestrator launched (`opencode run … --attach http://127.0.0.1:4096`) | |
| +1:05 | 00:44:14 | Branching logic → **MATCH** `workflow_run / prebuild-devcontainer / main / completed / success` → dispatch `project-setup` | |
| +3:11 | 00:46:20 | **Executing: pre-script-begin — `create-workflow-plan`** | |
| +11:12 | 00:54:21 | `create-workflow-plan` DONE — `plan_docs/workflow-plan.md` committed on branch `dynamic-workflow-project-setup` | ✅ |
| +11:47 | 00:54:56 | **Executing: main step — `init-existing-repository`** | |
| +17:36 | 01:00:45 | `init-existing-repository` DONE — **PR #1**, **Project #73** created/linked | ✅ |
| +18:31 | 01:01:40 | **Executing: post-assignment-complete (after init)** — `validate-assignment-completion` + `report-progress` **run in parallel** (QA test engineer + developer) | |
| +34:58 | 01:18:07 | post-assignment DONE | ✅ |
| +37:18 | 01:20:27 | **Executing: main step — `create-app-plan`** — notices Issue #2 already exists, *verifies* instead of duplicating (self-heal) | |
| +42:22 | 01:25:31 | `create-app-plan` DONE — Issue #2 verified as comprehensive | ✅ |
| +42:22 | 01:25:31 | **Executing: post-assignment-complete (after create-app-plan)** | |
| +47:25 | 01:30:34 | post-assignment DONE — validation **11/11 PASS** | ✅ |
| +47:36 | 01:30:45 | **Executing: main step — `create-project-structure`** | |
| +58:46 | 01:41:55 | `create-project-structure` DONE — commit `80b3a6b`, all checks pass | ✅ |
| +64:00 | 01:47:09 | post-assignment (after create-project-structure) DONE | ✅ |
| +64:49 | 01:47:58 | **Executing: main step — `create-agents-md-file`** | |
| +68:43 | 01:51:52 | `create-agents-md-file` DONE | ✅ |
| +73:35 | 01:56:44 | post-assignment (after create-agents-md-file) DONE | ✅ |
| +73:50 | 01:56:59 | **Executing: main step — `debrief-and-document`** | |
| +85:37 | 02:08:46 | `debrief-and-document` DONE — 12-section debrief, rating ⭐⭐⭐⭐ (4/5); **critical finding raised as Issue #3 (gitleaks secret leak)** | ✅ |
| +89:26 | 02:12:35 | post-assignment (after debrief) DONE | ✅ |
| +89:32 | 02:12:41 | **Executing: main step — `pr-approval-and-merge`** (special handling: self-approve, CI loop, delete branch, close setup issues) | |
| +90:40 | 02:13:49 | `devcontainer-opencode.sh exited with code: 0` — clean finish | |

## What made it succeed (patterns to replicate)

1. **Persistent checklist.** After every step the orchestrator re-printed its TODO list with
   `[ ]`→`[x]` transitions (visible at log lines 1163, 1226–1239, 1310–1324, 1398–1412, 1435–1448,
   1505–1516, 1538–1549, 1594–1604, 1621–1631, 1669–1678, 1702–1711). This kept the script on rails
   and is the single most reliable signal of a healthy run.
2. **Delegation to specialists, not solo execution.** Each assignment was delegated to a typed agent
   (e.g. *Developer Agent* for `create-workflow-plan`); validation went to an **independent QA test
   engineer** per the assignment rule ("Validation must be delegated to an independent quality agent").
3. **Parallel POC events.** `validate-assignment-completion` + `report-progress` ran **in parallel**
   after each main assignment, not serially.
4. **Fetch-then-execute.** Assignment definitions were fetched fresh from the canonical remote
   (`raw.githubusercontent.com/nam20485/agent-instructions/main/ai_instruction_modules/ai-workflow-assignments/<name>.md`)
   before each delegation — log lines 1070–1071, 1244.
5. **Self-healing.** When `create-app-plan` found Issue #2 already existed, it verified/supplemented
   it rather than creating a duplicate (lines 1370–1379).
6. **Recorded outputs.** Every step recorded its output at a script variable path
   (`#initiate-new-repository.<name>`, `#events.post-assignment-complete.<name>`), e.g. `init` →
   `PR #1, Project #73`; these carried forward (e.g. `$pr_num` into `pr-approval-and-merge`).
7. **Closing the loop.** The `issue opened` dispatch contract was honored: the orchestrator was
   expected to comment a summary and close the dispatch issue on success (contrast with the
   gap-miner run, where Issue #1 received *no* comment and stayed open).

## Trigger note (entry point differs from gap-miner)

- **foxtrot54 (this golden run):** entered via the **`workflow_run`** clause
  (`prebuild-devcontainer` success on `main`) → directly `/orchestrate-dynamic-workflow
  $workflow_name = project-setup`.
- **gap-miner-v2-delta48:** entered via the **`issues` / `opened`** clause — Issue #1
  "orchestrate-dynamic-workflow" with body `$workflow_name = project-setup`.

Both clauses resolve to the same `project-setup` workflow, so the entry point is not the cause of
the gap-miner stall; the stall was the orchestrator stopping partway through the script.

## How to read the raw log

Key anchors in [`traces/golden-path-foxtrot54.log.txt`](./golden-path-foxtrot54.log.txt):

| Line | What |
|------|------|
| 893 | "Hello, I am the Orchestrator Agent…" (run begins) |
| 876–923 | Clause evaluation → MATCH |
| 1009–1038 | Orchestrator's own restatement of the `project-setup` plan |
| 1122–1176 | pre-script-begin: `create-workflow-plan` |
| 1191–1239 | `init-existing-repository` (PR #1, Project #73) |
| 1263–1324 | first post-assignment-complete (parallel validate + report) |
| 1377–1412 | `create-app-plan` (+ self-heal on Issue #2) |
| 1451–1516 | `create-project-structure` |
| 1575–1604 | `create-agents-md-file` |
| 1634–1678 | `debrief-and-document` (Issue #3 gitleaks finding) |
| 1714–1728 | `pr-approval-and-merge`; exit code 0 |
