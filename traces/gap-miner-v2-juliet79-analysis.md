# Orchestration Run Analysis — `nam20485/gap-miner-v2-juliet79`

**Source trace:** `traces/gap-miner-v2-juliet79.log.txt` (2,130 lines)
**Trace window:** 2026-07-02 23:29:19 → 23:35:59 UTC (~6m40s)
**Run ID:** `f590b9da` (OpenCode server run)
**Repository:** https://github.com/nam20485/gap-miner-v2-juliet79 (created 2026-07-02T22:46:03Z)
**Models:** `zai-coding-plan/glm-4.7` (orchestrator/github-expert), `glm-5.1` (planner/developer/qa-test-engineer)

---

## 1. Executive Summary

A new repo was bootstrapped and dispatched into the **legacy webhook-receiver → OpenCode orchestrator**
pipeline (`project-setup` dynamic workflow). The `project-setup` workflow itself **succeeded** locally —
it wrote `plan_docs/workflow-plan.md`, `plan_docs/project-setup-roadmap.md`, and committed them on a
`dynamic-workflow-project-setup` branch.

However the run is dominated by three systemic defects that turned a single dispatch into a wasteful,
self-sustaining cascade:

1. **Webhook echo-loop** — the orchestrator's own status/close comments re-triggered the receiver, which
   has no `issue_comment` clause and no self-sender guard, so every comment spawned a new orchestrator run
   that fell through to `(default)` and posted *another* warning comment. Issue #1 ballooned to **7
   comments** from ~4 concurrent runs.
2. **Provider rate limiting** — the parallel cascade exhausted the `zai-coding-plan` request quota
   (`429 Rate limit reached for requests`).
3. **Corrupted memory-graph MCP store** — both `memory_*` and `memory-graph_*` tools failed on nearly
   every call with `Unexpected non-whitespace character after JSON`, because two concurrent MCP instances
   race-write the same `MEMORY_FILE_PATH` jsonl file.

**Net result:** the committed `project-setup` artifacts exist **only in the container workspace** — the
`dynamic-workflow-project-setup` branch was **never pushed** to GitHub, so none of the orchestrator's
work is visible on the remote. The QA subagent was still running when the trace was truncated.

---

## 2. Event Context (reconstructed from `EVENT_DATA`)

| Field | Value |
|-------|-------|
| Repo | `nam20485/gap-miner-v2-juliet79` (public, PowerShell, AGPL-3.0) |
| Repo created | 2026-07-02T22:46:03Z (initial commit pushed 22:46:12, size 0) |
| Issue #1 | title `orchestrate-dynamic-workflow`, body `/orchestrate-dynamic-workflow\n$workflow_name = project-setup` |
| Label | `orchestration:dispatch` |
| Dispatched workflow | `project-setup` |
| GitHub App installation | `137708514` |

The triggering event(s) captured in the trace are all of **type `issue_comment`, action `created`** —
i.e. comments that the orchestrator *itself* posted during the prior dispatch. The trace begins *after*
the original `issues`/dispatch event had already fired.

Delivery IDs seen (each = a distinct webhook → distinct fire-and-forget orchestrator run):

- `ae9af620-766d-11f1-9721-877393ff37bd` (trace:288)
- `01efd110-766e-11f1-8cbe-6d37b7460195` (trace:722)
- `ab2d46a0-766d-11f1-9bbf-a97273e165c2` (trace:817, 1287)

---

## 3. Timeline

| Time (UTC) | Event | Evidence |
|------------|-------|----------|
| 22:46:03 | Repo created | `created_at` in repo payload |
| 22:46:12 | Initial commit pushed (size 0) | `pushed_at` |
| 22:46:14 | Issue #1 created + labeled `orchestration:dispatch` | `created_at` |
| ~22:46–23:28 | Orchestrator matched dispatch clause → ran `project-setup` workflow | reconstructed; success comment confirms |
| 23:28:07 | 1st `(default)` warning comment posted ("no clause matched for this event…") | trace:244, comment id 4871279700 |
| 23:29:19 | **Trace begins** — orchestrator already processing an `issue_comment` run | trace:1 |
| 23:30:15 | Success comment: "✅ completed successfully… development-plan.md 757 lines… .NET SDK conflict… 6 assignments" | trace:1344, comment 4871288791 |
| 23:30:27 | "Status update posted. Issue closed…" + issue #1 **closed** | trace:1012, `closed_at` 23:30:27 |
| 23:30:32 | **Rate-limit errors** hit on 2 concurrent orchestrator streams | trace:750-751 |
| 23:30:54 | Orchestrator declares "🏁 Dispatch complete — project-setup finished with no errors. Goodbye!" | trace:801-803 |
| 23:30:55→23:32:04 | Cascading `(default)`-clause runs post repeated "no clause matched" comments; memory writes fail | trace:808-1339 |
| 23:33:15 | QA subagent (`qa-test-engineer`) spawned to validate completion | trace:1991-1995 |
| 23:35:59 | **Trace truncated** — QA subagent mid-step-9 (`mkdir -p docs/validation`), run not exited | trace:2122-2130 |

---

## 4. What Succeeded

- **`project-setup` workflow executed** and reported success (trace:801, 1344). It performed repo
  reconnaissance, analyzed `development-plan.md` (757 lines), flagged a `.NET SDK version conflict (R-1)`,
  and produced a 6-step setup roadmap.
- **Local artifacts created & committed**: `plan_docs/workflow-plan.md`, `plan_docs/project-setup-roadmap.md`
  on branch `dynamic-workflow-project-setup`, commit `85335c1`
  (`git checkout -b dynamic-workflow-project-setup` trace:888; `git commit -m "docs: add workflow execution
  plan for project-setup"` trace:1072/1179; commit inspected by QA at trace:2024/2056).
- **GitHub status updates posted** via `github-expert` subagent using `gh issue comment`
  (trace:752, 773 `✓ Post dispatch status to GitHub`).
- **Issue #1 closed** as `completed` (confirmed on GitHub: `state=closed, state_reason=completed`).

---

## 5. What Failed

### 5.1 Webhook echo-loop / cascade (PRIMARY)

The receiver dispatches the orchestrator fire-and-forget on **every** event type, including
`issue_comment`. The orchestrator prompt's branching logic contains **only** `issues.labeled` clauses
(trace:722 full prompt) — there is no `issue_comment` clause and **no guard to ignore comments authored by
the orchestrator/GitHub App itself**. Consequently:

1. The original dispatch posted a success comment + a close comment.
2. Each comment fired a new `issue_comment` webhook → a new orchestrator run.
3. Each run matched no clause → `(default)` → posted *another* warning comment.
4. → new webhook → repeat.

Evidence of the loop: the orchestrator repeatedly reasons that *"all clauses require `type = issues` but
received `type = issue_comment`"* (trace:175-181, 469, 516, 564, 1095, 1215) and the `(default)` clause's
`postStatusUpdate` is the driver (trace:597-599, 1266, 1295). Issue #1 comment count climbed
**4 → 6 → 7**; GitHub now shows **7 comments** and the issue is closed.

The orchestrator itself diagnosed this mid-run: *"The orchestrator workflow appears to have an unexpected
event handling mechanism… a comment seems to have activated the orchestrator. This suggests a potential
misconfiguration"* (trace:957, 1033).

### 5.2 Provider rate limiting (429)

Two concurrent orchestrator streams hit the request quota simultaneously:

```
trace:750  level=ERROR ... "stream error" ... modelID=glm-4.7 ... error.error="AI_APICallError: Rate limit reached for requests"
trace:751  level=ERROR ... "stream error" ... modelID=glm-4.7 ... error.error="AI_APICallError: Rate limit reached for requests"
```

Root cause is the cascade (5.1) fanning out many parallel `opencode run` sessions against the single
`zai-coding-plan` provider. (The streams did retry and recover at trace:759-762, so this was transient,
not fatal — but it directly throttled the active dispatch.)

### 5.3 Corrupted memory-graph MCP store

Every write/read to the `@modelcontextprotocol/server-memory` instances failed with JSON-parse errors. Two
distinct instances are configured (`memory_*` and `memory-graph_*` — both exposed, see tool list at
trace:1064/1271), and they share storage, racing on the same `MEMORY_FILE_PATH` jsonl and corrupting it.

```
trace:124  ✗ memory-graph_search_nodes … Error: Unexpected non-whitespace character after JSON at position 129 (line 1 column 130)
trace:818  ✗ memory-graph_create_entities … Error: … at position 129 (line 1 column 130)
trace:820  ✗ memory-graph_create_relations … Error: … at position 129 (line 1 column 130)
trace:909  ✗ memory-graph_add_observations … Error: … at position 129 (line 1 column 130)
trace:1307 ✗ memory_read_graph … Error: … at position 145 (line 1 column 146)
trace:1321 ✗ memory_search_nodes … Error: … at position 145 (line 1 column 146)
```

Impact: the **mandatory "update memory" completion step** largely failed. The orchestrator wasted many
turns retrying, then hallucinated tool names that don't exist:

```
trace:1063  ✗ Invalid Tool — Model tried to call unavailable tool 'memory_graph_add_observations'
trace:1271  ✗ Invalid Tool — Model tried to call unavailable tool 'read_graph'
```

Only the `memory_*` write variants occasionally succeeded (trace:808 `memory_create_entities` ✓,
trace:839 `memory_create_relations` ✓, trace:1115/1192), making the memory state inconsistent and
unreliable for future runs.

### 5.4 Work never pushed to GitHub (silent local-only commit)

The `project-setup` artifacts were committed locally but **never pushed**:

- `git checkout -b dynamic-workflow-project-setup` (trace:888)
- `git add plan_docs/workflow-plan.md` (trace:982)
- `git commit -m "docs: add workflow execution plan for project-setup"` (trace:1072, attempted again 1179)
- **No `git push` or `gh pr create` appears anywhere in the trace.**

GitHub confirms the failure: the remote has **only the `main` branch** (commits `8667a4f` Initial,
`e13d7e5` template-seed). There is **no `dynamic-workflow-project-setup` branch** and no commit `85335c1`
on the remote. The orchestrator declared "🏁 finished with no errors" (trace:801) despite never publishing
its work.

### 5.5 Redundant / split commits

The same commit message was issued twice:

```
trace:1072  git commit -m "docs: add workflow execution plan for project-setup"   (23:31:18)
trace:1179  git commit -m "docs: add workflow execution plan for project-setup"   (23:31:35)
```

This reflects multiple concurrent sessions (planner/developer/orchestrator) operating on the same shared
working tree (`/workspace/nam20485-gap-miner-v2-juliet79`) with no locking — interleaved commits to the
same branch with identical messages.

### 5.6 Trace truncated mid-run

The log ends at 23:35:59 with the `qa-test-engineer` subagent **still executing** (step 9,
`mkdir -p docs/validation`, trace:2122-2124) and no `exiting loop` / `Goodbye` for the main run. The run
was not cleanly terminated in the captured window — either logging was cut off or the process was killed.
This means final QA validation and any cleanup never completed.

---

## 6. Root-Cause Mechanism

```
                            ┌─────────────────────────────────────────────┐
   issues.labeled           │  dispatch clause runs project-setup → posts │
   orchestration:dispatch ─►│  success comment + closes issue #1          │
   (intended trigger)       └───────────────────┬─────────────────────────┘
                                                │ comments posted by the bot itself
                                                ▼
                        ┌──────────────────────────────────────────────────┐
                        │ issue_comment.created webhook(s) delivered       │
                        │ (no self-sender filter, no event-type allowlist) │
                        └───────────────────┬──────────────────────────────┘
                                            │ fire-and-forget dispatch (prompt.ps1)
                                            ▼
                        ┌──────────────────────────────────────────────────┐
                        │ New orchestrator run, type=issue_comment         │
                        │ → NO clause matches (all need type=issues)       │
                        │ → (default) clause → postStatusUpdate warning ◄──┤
                        └───────────────────┬──────────────────────────────┘
                                            │ new comment webhook
                                            └────────► (loop)
```

Three independent gaps combine into the cascade:

| Gap | Where | Effect |
|-----|-------|--------|
| No event-type filtering | webhook-receiver dispatch | `issue_comment` events reach the orchestrator |
| No `issue_comment` clause & no self-sender guard | orchestrator prompt branching logic | falls to `(default)` which posts a comment |
| `(default)` posts a comment | default-clause action | the comment re-triggers the receiver |

Compounding issues: the shared `/workspace` has no per-run isolation (concurrent commits, 5.5), the
memory store is non-concurrent-safe (5.3), and the provider has no concurrency headroom for a fan-out
(5.2).

---

## 7. Recommendations

1. **Stop the echo-loop (highest priority).** Either (a) have the receiver **ignore events generated by
   the GitHub App/orchestrator sender** (filter on `comment.user` / `sender.login` / app author
   association), or (b) **allowlist only the event types the branching logic handles** (e.g. `issues`
   with `labeled`) at the dispatch layer, or (c) add an `issue_comment` clause that is a no-op for
   orchestrator-authored comments. At minimum, the `(default)` clause must **not** post a comment.
2. **Make the receiver idempotent.** Dedup on `delivery_id` and per-(repo,issue,state) to prevent the same
   logical event from spawning overlapping runs.
3. **Fix the memory-graph store.** Stop running two concurrent `@modelcontextprotocol/server-memory`
   instances against the same `MEMORY_FILE_PATH`; consolidate to a single instance, or serialize writes.
   Repair the existing corrupted jsonl (`/app/.memory/memory.jsonl`).
4. **Push the branch / open the PR.** The `project-setup` (and `orchestrate-dynamic-workflow`) flow must
   push the working branch and open a PR before declaring success — currently "🏁 finished with no errors"
   is emitted with the work trapped in the container (no `git push`).
5. **Serialize working-tree access.** Concurrent orchestrator + planner + developer sessions committing to
   the same `/workspace` branch cause duplicate commits (5.5). Use distinct worktrees or a run-level lock.
6. **Add provider concurrency/rate-limit handling.** Back off / queue when `429` is hit rather than
   spawning N parallel streams against one provider/model.

---

## 8. Verification of Remote State (post-incident)

```
gh api repos/nam20485/gap-miner-v2-juliet79/branches        → [ main ]   (no dynamic-workflow-project-setup)
gh api repos/.../commits                                     → 8667a4f Initial commit
                                                              e13d7e5 Seed ... from template with plan docs
gh api repos/.../issues/1                                    → state=closed, state_reason=completed, comments=7
```

The orchestrator's local commit `85335c1` is **absent** from the remote — confirming the work was never
published.

---

# Appendix A — Comparison with the working GitHub-Actions version

Reference (known-good): `intel-agency/workflow-orchestration-service/.github/workflows/orchestrator-agent.yml`.
The orchestrator **prompt** (the `## Match Clause Cases` state machine) is identical between the two
versions — the same `orchestration:*` / `implementation:*` label clauses, same `postStatusUpdate`, same
`(default)` fallthrough. The difference is **where event filtering happens**.

## A.1 What the working version actually allows

`orchestrator-agent.yml` implements a **three-layer gate**, each layer narrowing the set:

**Layer 1 — trigger allowlist (`on:`, lines 3-13).** Only one event/action pair can even start a run:

```yaml
on:
  issues:
    types: [labeled]            # opened, edited, reopened, assigned are COMMENTED OUT
  #issue_comment:               # COMMENTED OUT  ← the loop-prevention belt
  #  types: [created, edited]
```

- `issue_comment.created` → **never fires a run** (commented out).
- `issues.opened` (issue created) → **never fires** (only `labeled` is live).

**Layer 2 — actor guard (`orchestrate` job `if`, lines 58-59).** `actor == 'traycerai[bot]'` is routed to
the `skip-event` job instead. The inline comment (line 29) states the intent verbatim:
*"traycerai[bot] actor — avoid feedback loops."*

**Layer 3 — label allowlist (`if`, lines 60-64).** Even a valid `issues.labeled` run is skipped unless the
label is `orchestration:*` (prefix) or exactly `implementation:ready` / `implementation:complete`.

The net effect: the **exact dispatch set is**

```
event = "issues"  AND  action = "labeled"
  AND  actor NOT endswith "[bot]"
  AND  ( label.name starts-with "orchestration:"
         OR label.name ∈ {"implementation:ready", "implementation:complete"} )
```

This is identical to the prompt's match-clause labels, so the transport gate and the agent gate agree.

## A.2 Side-by-side: old (Actions) vs new (webhook-receiver)

| Filter layer | Old — GitHub Actions | New — webhook-receiver (this run) |
|---|---|---|
| Event-type allow | `on: issues: [labeled]` — hard stop at trigger | `WEBHOOK_ALLOWED_EVENTS` checks **event name only** (`app.py:221`); `issue_comment` passes if listed; **allow-everything when unset** (`config.py:53-54`, `None` ⇒ no filter) |
| Action allow | `types: [labeled]` — `opened`/`closed`/`edited` can't fire | **none** — `issues.opened`/`issues.closed` dispatched too |
| Bot/self-actor guard | `actor != 'traycerai[bot]'` (`if:`, line 59) | **none** — `sender` is logged (`app.py:248`) but never checked |
| Label-relevance guard | `startsWith('orchestration:')` ∪ implementation labels | **none at transport** — only inside the agent prompt (soft) |
| Unmatched-event outcome | job never starts; `skip-event` logs it (line 41-54) | agent runs → `(default)` clause → **`gh issue comment`** → new webhook → loop |
| Where the agent even sees it | agent never instantiated for filtered events | agent always instantiated; filtering is advisory inside the LLM |

## A.3 Why the old design prevents exactly this run's failure

The echo-loop is structurally impossible in the Actions version because **the loop-closing event never
reaches the agent**:

```
old:   agent posts comment ─► GitHub fires issue_comment.created
                                   │
                                   ▼
                          on: does NOT include issue_comment
                                   │
                                   ▼
                          NO workflow run starts   ✅ loop broken at transport
```

```
new:   agent posts comment ─► GitHub App fires issue_comment.created
                                   │
                                   ▼
                          receiver has no issue_comment clause + no actor guard
                                   │
                                   ▼
                          dispatch_to_opencode() runs anyway (app.py:298)
                                   │
                                   ▼
                          agent hits (default) → posts ANOTHER comment ─► loop 🔁
```

Two specific behaviors from the failing run are **impossible** in the old design:

1. **The 7-comment cascade (failure 5.1).** Every comment the orchestrator posts is an
   `issue_comment.created` event; the old trigger set excludes that event entirely, so the cascade cannot
   start.
2. **The `429` rate-limit storm (failure 5.2).** It was caused by the cascade fanning out many parallel
   `opencode run` processes. With no cascade, only one run fires per genuine `issues.labeled`.

The label + actor guards additionally prevent label-driven self-loops (an agent applying a workflow label
to re-trigger itself) — the equivalent of the `traycerai[bot]` skip.

---

# Appendix B — Implementing the identical gate in the webhook-receiver

Goal: make the receiver's dispatch decision **exactly match** the old `orchestrate` job `if:` — same event
set, same action, same labels, same bot guard — evaluated *before* `dispatch_to_opencode()` so the agent is
never spawned for filtered events.

## B.1 Current state (what to change)

- `webhook_receiver/app.py:221-235` — the only filter. It checks event **name** membership in
  `cfg.allowed_events`. It does **not** check action, label, or actor. When `WEBHOOK_ALLOWED_EVENTS` is
  unset it permits **everything** (`config.py:53`).
- `webhook_receiver/filters.py` — currently filters *trace log lines*, not webhook events. Reuse it as the
  home for the new webhook predicate so all filtering lives in one module.
- The agent prompt (`prompts.py`) is left unchanged — its match clauses already encode the same labels; we
  are tightening the transport so the agent is never asked to handle an event it can't match.

## B.2 The dispatch predicate (new, in `filters.py`)

Mirror the old `orchestrate` job `if:` one-for-one:

```python
# webhook_receiver/filters.py  — add below the existing trace-line filter

from typing import Any

# --- Exact replica of orchestrator-agent.yml's orchestrate-job gate ---
_EVENT_ALLOW   = {"issues"}
_ACTION_ALLOW  = {"labeled"}
_LABEL_PREFIXES = ("orchestration:",)
_LABEL_EXACT    = {"implementation:ready", "implementation:complete"}


def _is_workflow_label(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.startswith(p) for p in _LABEL_PREFIXES) or n in _LABEL_EXACT


def should_dispatch(
    event: str, payload: dict[str, Any]
) -> tuple[bool, str]:
    """Return (allow, reason). Must mirror the GitHub Actions orchestrate-job `if:`.

    Allow set:  issues + labeled + non-bot actor + workflow-relevant label.
    Everything else is rejected at the transport — the agent is never spawned.
    """
    event = (event or "").lower()
    action = (payload.get("action") or "").lower()

    # Layer 1 — event type
    if event not in _EVENT_ALLOW:
        return False, f"event {event!r} not dispatched (only {sorted(_EVENT_ALLOW)})"
    # Layer 2 — action subtype
    if action not in _ACTION_ALLOW:
        return False, f"{event}.{action!r} not dispatched (only {sorted(_ACTION_ALLOW)})"
    # Layer 3 — bot / self-actor guard (anti-loop)
    sender = ((payload.get("sender") or {}).get("login") or "").lower()
    if sender.endswith("[bot]") or sender.endswith("-bot"):
        return False, f"bot actor {sender!r} skipped (anti-loop)"
    # Layer 4 — label relevance. For issues.labeled the applied label is top-level.
    label_name = ((payload.get("label") or {}).get("name") or "")
    if not _is_workflow_label(label_name):
        return False, f"label {label_name!r} not workflow-relevant"
    return True, "allowed"
```

## B.3 Wire it into the handler (`app.py`)

Insert the gate **right after** the JSON parse (so `payload` exists) and **before** prompt assembly /
`background_tasks.add_task(...)`. Place it alongside the existing `allowed_events` block:

```python
# app.py — after `payload = json.loads(body)` (current line ~238), before build_orchestrator_prompt

allow, reason = should_dispatch(event, payload)
if not allow:
    logger.info(
        "Filtered delivery_id=%s event=%s action=%s — %s",
        delivery_id, event, payload.get("action"), reason,
    )
    store.emit(
        "webhook_filtered",
        delivery_id=delivery_id, event=event,
        action=payload.get("action", ""), reason=reason,
    )
    return JSONResponse(
        {"status": "ignored", "delivery_id": delivery_id,
         "event": event, "reason": reason},
        status_code=202,
    )
```

This turns the `(default)`-clause cascade off at the source: an `issue_comment.created` (or an
`issues.opened`, or a bot actor, or an irrelevant label) returns `202 ignored` and never reaches
`dispatch_to_opencode()`.

## B.4 Config / env alignment

- Keep `WEBHOOK_ALLOWED_EVENTS` as a **coarse first** sieve (e.g. set it to `issues` only in compose), but
  treat `should_dispatch()` as the authoritative gate — it is the precise equivalent of the Actions `if:`.
- No new env vars required; the allowed set is a code constant so it cannot drift from the prompt's
  match-clause labels. (If runtime tunability is wanted, wrap the four constants in `Settings`, but default
  them to the values above so a misconfigured env var can't re-open the loop.)

## B.5 What this guarantees vs the failing run

| Failing-run symptom | Status after gate |
|---|---|
| `issue_comment.created` dispatched → `(default)` → comment → loop (5.1) | **Eliminated** — Layer 1 rejects `issue_comment` |
| `issues.closed`/`issues.opened` noise dispatched | **Eliminated** — Layer 2 keeps only `labeled` |
| Bot/self comments re-triggering (5.1) | **Eliminated** — Layer 3 skips `[bot]`/`-bot` actors |
| Irrelevant-label `issues.labeled` spawning the agent | **Eliminated** — Layer 4 keeps only workflow labels |
| Parallel-cascade `429` storm (5.2) | **Eliminated** — no cascade ⇒ no fan-out |
| `(default)` clause ever posting a comment | **Unreachable** for transport-filtered events |

The memory-graph corruption (5.3) and the missing `git push` (5.4) are independent bugs to fix separately,
but neither can recur as a *cascade* once the transport gate is in place.
