# Orchestration Run Failure Report — gap-miner-v2-golf86 Issue #1

**Date:** 2026-07-22
**Status:** FAILED — Watchdog idle-timeout kill (work actually succeeded post-kill)
**Severity:** Medium (data was created; cleanup/manual-close required)

---

## 1. Run Identity

| Field | Value |
|---|---|
| Run ID | `7b1966ca-2f08-4b82-ace0-4f7c80cd6e09` |
| Process PID | 156 |
| Webhook delivery ID | `54712c20-85d4-11f1-933e-e231d882c0ca` |
| Trigger event | `issues.labeled` |
| Repository | `nam20485/gap-miner-v2-golf86` |
| Issue | #1 — "gh-issue-tracking-init" |
| Trigger label | `gh-issue-tracking:direct-body` |
| Matched clause | `gh-issue-tracking:direct-body` (clause 7) |
| Issue body (dispatched verbatim) | `/gh-issue-tracking-init` |
| Model used | `zai-coding-plan/glm-5` (**should have been `glm-5.2`**) |
| Agent | `orchestrator` |
| Prompt stem | `prompt-nam20485__gap-miner-v2-golf86__issue-1__create-epic-v2__20260722T135043Z-xmi8nb5m` |
| stdout trace | `/tmp/orchestrator-webhook/<stem>.stdout` (402 lines) |
| stderr trace | `/tmp/orchestrator-webhook/<stem>.stderr` (707 lines) |
| Watchdog config | idle_timeout=900s, hard_ceiling=5400s, poll=30s, max_errors=5 |
| Kill reason | `idle_timeout` (line_idle=913s, threshold=900s, elapsed=1140s) |

---

## 2. Timeline

All times UTC (container local). Durations approximate where noted.

| Time (UTC) | Elapsed | Event |
|---|---|---|
| 13:50:40 | — | Issue #1 created in GitHub by `nam20485` |
| 13:50:43 | 0s | **Run dispatched** (pid=156); watchdog started |
| 13:50:45 | 2s | opencode DB migration; CLI args confirm `--model zai-coding-plan/glm-5 --thinking true --dangerously-skip-permissions true` |
| 13:50:47 | 4s | opencode CLI banner: `orchestrator · glm-5` |
| 13:51:10 | 27s | First agent reasoning output; begins mandatory memory-load step |
| 13:51:39 | 56s | `sequentialthinking` MCP call **fails** — `MCP error -32602: Input validation error: Invalid arguments` (missing `nextThoughtNeeded` field) |
| 13:52:11 | 88s | `sequentialthinking` retry **succeeds** (with `nextThoughtNeeded:true`) |
| 13:52:23 | 100s | Prints EVENT_DATA summary; confirms clause 7 match |
| 13:52:38 | 115s | Notes `.github/` is empty — skill is self-contained, proceeds |
| 13:52:50 | 127s | Reads `SKILL.md` for `gh-issue-tracking-init` |
| 13:52:54 | 131s | Posts opening status comment to issue #1 |
| 13:53:29 | 166s | Finalizes plan to dispatch `github-expert` subagent |
| 13:54:30 | 227s | **Dispatches subagent**: `Run gh-issue-tracking-init skill` → `Github-Expert Agent` |
| ~13:54:31 | ~228s | **Last stdout/stderr line** — process goes silent |
| 13:55:44 | 300s | Watchdog heartbeat: `line_idle=73s, lines=707` |
| 13:56–14:09 | 300–1140s | 30 heartbeat lines, all showing `lines=707` (frozen) and growing `line_idle` |
| **14:09:44** | **1140s** | **WATCHDOG IDLE TIMEOUT** — `line_idle=913s ≥ threshold=900s` → SIGTERM → SIGKILL |
| 14:09:44 | 1140s | Pre-termination diagnostics dumped; failure comment posted to issue #1 |
| 14:13:16 | +3.5min | **Orphaned `gh` processes create issue #2** (Plan: Gap Mining Platform) |
| 14:13:19–14:14:40 | +4–5.5min | Orphaned processes create issues **#3–#31** (7 epics + 22 stories) |
| 14:20:35 | — | *Separate* run (pid=343) starts for `gh-issue-tracking:init-success` clause on issue #2 — succeeds |

**Total run duration:** 19 min 0s (1140s)
**Active-output duration:** ~3 min 48s (228s)
**Silent duration (subagent running):** ~15 min 12s (913s)

---

## 3. What the Run Was Trying to Do

The `gh-issue-tracking:direct-body` clause dispatched issue #1's body (`/gh-issue-tracking-init`) verbatim as a prompt. This loads the `gh-issue-tracking-init` skill, which builds a GitHub issue-tracking hierarchy from a development plan:

- **Plan issue** (parent)
- **Epic issues** (one per phase)
- **Story issues** (one per plan task/line-item)
- **Labels, milestones, and Projects v2 board**

The orchestrator analyzed the plan document (`plan_docs/development-plan.md` — 793 lines), extracted the structure (7 phases → 7 epics, 22 stories, no task sub-tier), then delegated execution to the `github-expert` subagent.

---

## 4. Root Cause: Watchdog Can't See Subagent Activity

### The mechanism

The idle watchdog (`webhook_receiver/watchdog.py`) monitors the opencode CLI process's **stdout/stderr line freshness** as its sole activity signal. Stream-reader threads call `WatchdogState.record_line()` on every line received; the watchdog polls every 30s and kills the process when no line has been received for `IDLE_TIMEOUT_SECS` (900s / 15 min).

When the orchestrator dispatches a subagent via opencode's `task` tool:

1. The parent (orchestrator) agent **blocks** waiting for the subagent result.
2. The subagent runs internally within the opencode server. Its thinking, tool calls, and intermediate progress are **buffered server-side** — they are **NOT streamed** to the CLI's stdout/stderr.
3. The parent process produces **zero output** for the entire subagent execution duration.
4. The watchdog sees this as "idle" (no line for 900s) and kills the process.

The subagent was **actively and successfully working** the entire time — reading the plan, running PowerShell scripts, and creating GitHub issues via `gh` CLI. But none of that activity was visible to the watchdog.

### Evidence

- The last captured line (line 707) was the subagent dispatch: `Run gh-issue-tracking-init skill  Github-Expert Agent`
- Line count froze at **707** for the entire 15-minute silent period (30 heartbeat polls confirm this)
- `consecutive_errors=0` throughout — no error storm, just silence
- The subagent's orphaned processes **successfully created all 30 issues** (#2–#31) 3–5 minutes **after** the kill, proving the work was in-flight and progressing when terminated

### Why the work survived the kill — CORRECTED

> **Initial analysis (incorrect):** The work survived because orphaned `gh` CLI grandchild processes continued after the client PID was killed.
>
> **Corrected analysis (verified via server logs):** The work survived because **killing the opencode CLIENT does not stop the opencode SERVER.** The current architecture runs the client (`opencode run --attach`) and server (`opencode serve`) in separate Docker containers. The watchdog killed the client (pid=156 in the `webhook-receiver` container) but the server (pid=1 in the `orchestratorservice` container) kept the agent session alive and completed the entire run to success.

The server log at `/home/app/.local/share/opencode/log/opencode.log` (run `da1f0df9`, single server-side run ID) shows the full timeline:

| Server time | Session | Agent | Event |
|---|---|---|---|
| 13:50:47 | `ses_075e828e` | orchestrator (primary) | Session starts — client connected, output streaming |
| 13:54:30 | `ses_075e4ba5` | github-expert (subagent) | Subagent dispatched by orchestrator |
| 13:54:30 – 14:12:30 | `ses_075e4ba5` | github-expert (subagent) | Subagent runs ~38 LLM steps: reads plan, runs scripts, creates issues |
| **14:09:44** | — | — | **CLIENT (pid=156) killed by watchdog** — server does not notice |
| 14:09:07 – 14:12:30 | `ses_075e4ba5` | github-expert (subagent) | Subagent continues steps 30–38 **after client kill** |
| ~14:13–14:14 | `ses_075e4ba5` | github-expert (subagent) | Creates issues #2–#31, applies `gh-issue-tracking:init-success` label to #2 |
| **14:29:54** | `ses_075e828e` | orchestrator (primary) | **Orchator resumes — 20 min after client kill** — processes subagent result |
| 14:31:17 | `ses_075e828e` | orchestrator (primary) | Posts "✅ Direct-body execution completed" comment to issue #1 |
| 14:31:17 | `ses_075e828e` | orchestrator (primary) | Closes issue #1 (now CLOSED state) |
| 14:32:12 | `ses_075e828e` | orchestrator (primary) | Session exits loop — **run complete** |

**Issue #1 final state:** CLOSED, 3 comments:
1. `13:52:54` — Orchestrator opening status (client-era)
2. `14:09:44` — Runner failure comment ("❌ did not complete")
3. `14:31:17` — Orchestrator success comment ("✅ completed") — **posted 22 min after client kill, by the server-side session**

**Implication:** The watchdog's kill is **functionally a no-op** for the actual work. It kills the output stream (the client) but the server-side agent session continues to completion, including posting comments, applying labels, and closing issues. The runner sees the client exit as failure and posts an inaccurate failure comment, while the server-side session succeeds anyway.

The `gh-issue-tracking:init-success` label on issue #2 was applied by the **github-expert subagent** (running the gh-issue-tracking-init skill server-side) as its final step — not by the orchestrator, not by a retry, and not by the runner. That label triggered a *separate* webhook event, which launched run pid=343 (the `init-success` acknowledgment clause).

---

## 5. Contributing Factors

### 5.1 Model mismatch — ran `glm-5` instead of `glm-5.2`

The CLI was invoked with `--model zai-coding-plan/glm-5`, but the intended model is `glm-5.2`. The CLI `--model` flag always overrides the `opencode.json` config-file default. The effective default lives in `webhook_receiver/config.py:107`:

```python
model=os.environ.get("OPENCODE_MODEL", "zai-coding-plan/glm-5"),
```

The fallback `glm-5` is stale; `OPENCODE_MODEL` is not set in the environment, so `glm-5` was used. This is a known issue (the model-plumbing chain is `config.py → runner.py:_base_args → prompt.ps1 → opencode run`). The `glm-5` model is slower and less capable than `glm-5.2`, which may have contributed to the subagent taking longer than expected.

### 5.2 Early `sequentialthinking` MCP validation error (recovered)

At 13:51:39, the first `sequentialthinking` call failed with `MCP error -32602: Input validation error` — it omitted the required `nextThoughtNeeded` field. The agent self-corrected and retried successfully 9 seconds later. This consumed ~30s but was not fatal. The watchdog recorded it as the `last_error_message` in pre-termination diagnostics (a red herring — it was unrelated to the timeout).

### 5.3 Single-PID kill leaves orphaned processes

`start_new_session=True` creates a new process group, but `_terminate()` does not use `os.killpg()` to kill the whole group. Only `proc.terminate()` / `proc.kill()` (targeting the single PID) is called. Grandchild processes survive.

### 5.4 No subagent progress streaming in opencode v1.15.13

The opencode CLI (v1.15.13) does not stream subagent progress to the parent's stdout/stderr during `task` execution. This makes the watchdog's line-freshness signal blind to subagent work — the fundamental incompatibility.

---

## 6. Consequences

| Impact | Details |
|---|---|
| **Premature failure comment** | The runner posted "❌ did not complete" at 14:09:44 when the client was killed, but the server-side run actually completed successfully 22 min later. |
| **Contradictory comments on issue #1** | Issue #1 ended up with BOTH a failure comment (14:09:44) and a success comment (14:31:17), plus it was closed — a confusing timeline for any human reviewer. |
| **Duplicate work risk** | If a retry mechanism had re-dispatched the run based on the failure, the gh-issue-tracking-init skill would have run again (it's idempotent, so no data corruption, but wasted resources). |
| **Data integrity OK** | All 30 issues (#2–#31) were created correctly: 1 plan, 7 epics, 22 stories. Labels, hierarchy, and ordering are intact. Issue #1 was closed. |

> **Note:** The original analysis stated issue #1 was left OPEN. That was true at the time of initial investigation (~14:20), but the server-side orchestrator session later completed and closed it at 14:31:17. The failure comment remains, creating a contradictory issue history.

---

## 7. Recommendations

### 7.1 Watchdog must account for subagent execution (root cause fix)

The watchdog needs a way to distinguish "process is stuck" from "process is blocked on a long-running subagent." Options:

- **A. Process-group I/O monitoring:** Instead of (or in addition to) the CLI's stdout/stderr, monitor I/O counters of the entire process group (including grandchildren). Active `gh` CLI calls would register as activity.
- **B. Heartbeat from subagents:** Configure opencode to emit periodic heartbeat lines to stdout during subagent execution (e.g., `task: github-expert running...`).
- **C. Increase idle timeout for dispatch-heavy clauses:** Raise `IDLE_TIMEOUT_SECS` for clauses known to dispatch long-running subagents (direct-body, dispatch). The current 900s is too aggressive for subagent-heavy workflows.
- **D. Exclude subagent-dispatch lines from idle reset:** When the watchdog sees a `task`/subagent dispatch line, switch to the hard-ceiling timer instead of the idle timer for the remainder of the run.

### 7.2 Kill the entire process group on timeout

Change `_terminate()` in `watchdog.py` to use `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` (then SIGKILL after grace) instead of `proc.terminate()`. Since `start_new_session=True` is already set, the process group is well-defined. This prevents orphaned grandchildren from continuing to mutate GitHub state after the orchestrator is killed.

### 7.3 Fix the stale model default

Update `webhook_receiver/config.py:107` to default to `zai-coding-plan/glm-5.2` instead of `glm-5`, or set `OPENCODE_MODEL` in the compose environment.

### 7.4 Post-kill reconciliation

Add a reconciliation step after a watchdog kill: check whether the dispatched work (e.g., GitHub issues created, PRs opened) actually completed despite the kill, and update the failure comment accordingly. This prevents misleading "did not complete" comments when the work succeeded post-kill.

### 7.5 Direct-body close-on-success

The runner's close-on-success incomplete-detection (`runner.py:609`) is gated to `orchestration:dispatch` only. The `gh-issue-tracking:direct-body` clause should implement its own publish/verify + close step so successful direct-body runs close the triggering issue even if the orchestrator is killed mid-run.

---

## 8. Verification Artifacts

- **Container:** `orchestrator-service-webhook-receiver-1` (Up 33 min at time of analysis)
- **stdout trace:** 402 lines — ends with orchestrator's dispatch reasoning, no subagent output captured
- **stderr trace:** 707 lines — ends with `Run gh-issue-tracking-init skill  Github-Expert Agent`
- **Watchdog heartbeat log:** 30 polls from 13:55:44 to 14:09:44, all showing frozen `lines=707`
- **GitHub issue timestamps:** #2 created 14:13:16Z, #31 created 14:14:40Z — all post-kill
- **Issue #1 final state:** OPEN, 2 comments (status update + failure comment), `gh-issue-tracking:direct-body` label

---

## 9. Cross-Project Analysis — Previous System Already Solved This

### 9.1 This is a known, previously-solved regression

The predecessor system (`intel-agency/workflow-orchestration-service`) documented and **fully resolved** this exact failure mode. The forensic report at `docs/.archived/idle-timeout-forensic-report.md` (dated 2026-03-26) describes **5 consecutive orchestrator failures** across 4 repos, all idle-timeout kills during subagent delegation — the identical pattern observed here. The solution was implemented and validated in `run_opencode_prompt.sh` (commit `22f0b94`, 2026-03-28).

**The current system (`orchestrator-service`) regressed** by porting the watchdog from bash to Python but dropping the multi-signal activity detection that made subagent delegation survivable.

### 9.2 How the previous system solved it (3 layers)

The previous watchdog (`run_opencode_prompt.sh`, lines 165–444) used **three independent activity signals** in a tiered AND-gate: the process was only killed when **all** signals were simultaneously stale.

#### Layer 1 — Server process I/O counters (`/proc/<pid>/io`)

The opencode server ran in the **same container** as the client, so `/proc/<server_pid>/io` was readable. The watchdog tracked `read_bytes` and `write_bytes` **separately** with distinct semantics:

```
write_bytes changing  → strong progress signal (DB writes, tool results). Fully resets idle timer.
read_bytes changing   → weaker "alive" signal (API response ingestion). Grants READ_ONLY_GRACE_SECS (1200s).
neither changing      → truly idle.
```

This was the primary mechanism: during subagent execution, the **server process** was actively doing I/O (SQLite writes, LLM streaming) even though the **client process** produced no stdout. The watchdog saw server I/O activity and withheld the kill.

```
_read_server_io_split() {
    spid=$(cat "$SERVER_PIDFILE")
    awk '/^read_bytes:/{r=$2} /^write_bytes:/{w=$2} END{print r, w}' "/proc/$spid/io"
}
```

#### Layer 2 — Server log file tailer (live streaming to stdout)

The server ran with `--log-level DEBUG --print-logs`, writing structured log entries (tool calls, session events, agent dispatches) to `/tmp/opencode-serve.log`. A background `tail -f` + `grep -Ev` pipeline streamed **filtered** server log lines to CI stdout with `[server]` prefix:

```bash
tail -f -n +$(( _server_log_start_lines + 1 )) "$SERVER_LOG" > "$_server_log_pipe" &
grep -Ev "$_SERVER_LOG_NOISE" < "$_server_log_pipe" | grep -v '^\s*$' | sed -u 's/^/[server] /' &
```

This served two purposes: (a) the streamed lines kept the watchdog's client-stdout idle timer reset, and (b) operators could see what the subagent was doing in real time. A carefully-tuned noise filter (`_SERVER_LOG_NOISE`) suppressed per-token chatter (~15 regex patterns for `service=bus`, `service=permission`, etc.).

#### Layer 3 — Server log mtime fallback

When `/proc/io` was unavailable (non-Linux, permissions), the watchdog fell back to checking the server log file's modification time:

```bash
server_last_mod=$(stat -c %Y "$SERVER_LOG")
server_log_idle=$(( now - server_last_mod ))
```

The idle decision was the **max** of client-output-idle and server-idle — both had to be stale:

```bash
if [[ $output_idle -le $server_idle ]]; then idle=$output_idle; else idle=$server_idle; fi
if [[ $idle -ge $IDLE_TIMEOUT_SECS ]]; then kill; fi
```

### 9.3 Why the current system regressed

| Aspect | Previous system (`intel-agency`) | Current system (`orchestrator-service`) |
|---|---|---|
| **Container topology** | Client + server in **same** container (GitHub Actions runner) | Client + server in **separate** Docker containers |
| **Activity signal 1** | `/proc/<server_pid>/io` read/write bytes | ❌ Dropped — `/proc` is cross-container inaccessible |
| **Activity signal 2** | Server log tailer streaming to stdout | ❌ Dropped — server log is in the `orchestratorservice` container, not accessible from `webhook-receiver` |
| **Activity signal 3** | Server log mtime fallback | ❌ Dropped — same reason |
| **Sole remaining signal** | (n/a — three layers) | Client stdout/stderr line freshness **only** |
| **Idle decision logic** | `idle = max(client_idle, server_idle)` — AND gate | `idle = client_idle` — single signal |
| **Kill scope** | Single PID (OK — co-located, grandchildren die with parent) | Single PID (BUG — `start_new_session=True` orphans grandchildren) |

The current `watchdog.py` docstring (lines 8–13) acknowledges the `/proc` constraint: *"The new system runs them in separate Docker containers, so /proc is inaccessible. Instead the watchdog monitors the opencode CLI's stdout/stderr line freshness."* — but this left no signal during subagent execution, reintroducing the exact bug the previous system spent significant effort fixing.

### 9.4 What's portable to the current system

The server log tailer (Layer 2) is **directly portable** with one architectural change. Confirmed by inspecting the live server log:

```
docker exec orchestrator-service-orchestratorservice-1 \
  tail /home/app/.local/share/opencode/log/opencode.log
```

The server log **does contain rich, real-time subagent activity** — tool calls, permission evaluations, session loop steps, LLM streaming events — all timestamped. During subagent execution this log is actively written. The watchdog just can't see it because it's in the other container.

**Required change:** Share the opencode log directory between containers via a Docker volume, then add a log-tailer thread in the Python watchdog.

### 9.5 Adapted implementation plan for the current system

> **Critical correction based on server-log forensics (§4):** The watchdog kill of the client is a functional no-op — the server-side agent session continues to completion regardless. This means the primary fix is **not** "kill harder" (process groups, etc.) but **"see the server activity so the watchdog never fires."** The server-log monitoring (Steps 1–2) is the root fix; the kill improvements (Steps 3–4) are secondary hardening for genuine stalls.

#### Step 1 — Share the server log volume (compose.yaml) [ROOT FIX]

```yaml
services:
  orchestratorservice:
    volumes:
      - opencode-logs:/home/app/.local/share/opencode/log
  webhook-receiver:
    volumes:
      - opencode-logs:/home/app/.local/share/opencode/log:ro

volumes:
  opencode-logs:
```

This makes `/home/app/.local/share/opencode/log/opencode.log` readable from the webhook-receiver container (read-only). Confirmed viable — the server log contains rich, real-time activity (tool calls, session loop steps, LLM streaming) that would have prevented this kill entirely.

#### Step 2 — Add server-log activity signal to the Python watchdog [ROOT FIX]

If the watchdog can see the server log is being actively written (new lines / mtime changes), it knows the agent is working and withholds the kill. Two options, simplest first:

**Option A — Log mtime (minimal, ~15 lines in `watchdog.py`):**

```python
# In IdleWatchdog.run(), alongside line_idle check:
server_log_path = Path("/home/app/.local/share/opencode/log/opencode.log")
if server_log_path.exists():
    server_log_mtime = server_log_path.stat().st_mtime
    server_log_idle = time.monotonic() - server_log_mtime
    effective_idle = min(line_idle, server_log_idle)  # active if EITHER signals
else:
    effective_idle = line_idle  # fallback to current behavior
# Kill only when effective_idle >= idle_timeout_secs
```

This mirrors the previous system's Layer 3 fallback. Simple, robust, no threading.

**Option B — Log tailer thread (full parity, ~40 lines):**

Add a daemon thread that tails the server log, filters noise, and calls `state.record_line()` on each meaningful line — making the existing line-freshness watchdog see server activity as if it were client output. This mirrors the previous system's Layer 2 exactly.

```python
def _tail_server_log(state: WatchdogState, log_path: Path, stop_event: threading.Event):
    noise_patterns = re.compile(r"service=bus |service=permission |...")
    with log_path.open() as f:
        f.seek(0, 2)  # seek to end
        while not stop_event.is_set():
            line = f.readline()
            if line:
                if not noise_patterns.search(line):
                    state.record_line(f"[server] {line}")
            else:
                time.sleep(0.5)
```

#### Step 3 — Kill the server-side session, not just the client [CRITICAL]

When a genuine stall IS detected (both client AND server are idle), killing the client alone doesn't stop the server-side session. The watchdog must also abort the server-side session via the opencode server API. Without this, a killed run's server-side session can continue for 20+ minutes consuming API credits and mutating GitHub state.

Investigate whether `opencode serve` exposes a session-abort/session-cancel HTTP endpoint (the `--attach` client likely has a disconnect mechanism). If not, the server process itself may need to be signalled or the session abandoned (it will eventually time out on its own, but that wastes resources).

#### Step 4 — Process-group kill (defense in depth)

Even with server-log monitoring, the client kill should target the entire process group, not just the PID. Since `start_new_session=True` creates a new session, the PGID equals the PID:

```python
def _terminate(self, reason: str) -> int:
    import os, signal
    try:
        pgid = os.getpgid(self._proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        self._proc.wait(timeout=self._config.sigterm_grace_secs)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self._proc.wait()
    return self._proc.returncode if self._proc.returncode is not None else -15
```

#### Step 5 — Bump idle timeout as immediate stopgap

```yaml
# compose.yaml
IDLE_TIMEOUT_SECS: ${IDLE_TIMEOUT_SECS:-1800}   # 30 min (was 900)
```

The previous system's forensic report recommended 1800s (30 min) as the immediate unblock. This is zero-risk and prevents the failure while Steps 1–3 are implemented.

---

## 10. Source References

| Document | Path | Relevance |
|---|---|---|
| Forensic report (identical bug) | `intel-agency/workflow-orchestration-service/docs/.archived/idle-timeout-forensic-report.md` | Root cause analysis, 7 solution options, recommended combined approach |
| Subagent tracing options | `intel-agency/workflow-orchestration-service/docs/.archived/opencode-subagent-tracing/subagent-tracing-options-report.md` | 6 tracing strategies, tiered implementation plan, Phase 2 implementation status |
| Observability guide | `intel-agency/workflow-orchestration-service/docs/.archived/opencode-subagent-tracing/Subagent Observability Guide.md` | Headless tracing flags, session correlation, heartbeat monitoring |
| **Actual watchdog implementation** | `intel-agency/workflow-orchestration-service/run_opencode_prompt.sh` (lines 165–524) | The real bash code: `/proc/io` split tracking, server log tailer, tiered idle decision |
| **Actual server startup** | `intel-agency/workflow-orchestration-service/scripts/start-opencode-server.sh` | Server log path, PID file, `--print-logs` flag |
| **Watchdog I/O tests** | `intel-agency/workflow-orchestration-service/test/test-watchdog-io-detection.sh` | Test cases for read/write split detection, edge cases |
| Current watchdog (regressed) | `orchestrator-service/webhook_receiver/watchdog.py` | Python port with stdout/stderr-only signal |
| Current runner (spawn config) | `orchestrator-service/webhook_receiver/runner.py:717` | `start_new_session=True` without process-group kill |
