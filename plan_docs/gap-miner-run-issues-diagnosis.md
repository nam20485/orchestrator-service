# Diagnosis: gap-miner-v2-papa85 Run Issues

Run ID: `70c74cd7` | Repo: `nam20485/gap-miner-v2-papa85` | Date: 2026-07-11

---

## Issue 1: Idle Timeout Watchdog — Redesign

### 1.1 Evidence from the gap-miner Run

| Timestamp | Event | Source |
|-----------|-------|--------|
| ~07:30:55 | Run dispatched (first orchestrator log line) | `gha_workflow_output.log:3` |
| 08:15:55 | **Expected DISPATCH_TIMEOUT kill** (07:30:55 + 2700s) | computed |
| 08:39:14 | First API rate-limit error (process still alive, 68 min in) | `gha_workflow_output.log:2359` |
| 08:39:48 | Last API rate-limit error (5 consecutive, exponential backoff) | `gha_workflow_output.log:2371` |
| ~08:39:50+ | User hits `^C`, runs `docker compose down` | `gha_workflow_output.log:2375-2382` |

The process ran for **~69 minutes** — 24 minutes past the 45-min `DISPATCH_TIMEOUT_SECS` — yet no `TimeoutExpired` kill occurred. No `Run summary` line was ever emitted, confirming `_run_completion_watcher` never reached its post-exit classification logic.

### 1.2 Current Implementation and Its Gaps

The current timeout is a single `proc.wait(timeout=DISPATCH_TIMEOUT_SECS)` call in a daemon thread (`runner.py:402-408`). Problems:

1. **Wall-clock only, no activity detection** — a process that is actively working for 44 minutes then stalls for 1 minute gets killed, while a process that produces zero output for 44 minutes but is "alive" does not. The timeout measures elapsed time, not idle time.

2. **Silent failure mode** — if `settings.dispatch_timeout` resolves to `None` (env var not set), the `else` branch at `runner.py:409` calls `proc.wait()` with no timeout — infinite wait. No log line confirms which branch was taken.

3. **No live heartbeat tracing** — the old system emitted `[watchdog]` heartbeats every 30s showing `elapsed`, `output_idle`, `server_idle`, `effective_idle`, `write_active`, `read_active`, `write_bytes`, `read_bytes`. The new system emits nothing until the process exits. Post-mortem diagnosis is impossible without these.

4. **No pre-termination diagnostics** — when the old system killed a process, it dumped the last 80 lines of the server log. The new system posts a one-line failure comment with no context about what the process was doing before it died.

5. **No mid-run error detection** — the gap-miner run hit 5 consecutive `AI_APICallError: Usage limit reached` errors (`runner.py`'s stderr stream) but the runner had no mechanism to detect and abort early. The process would have retried indefinitely until the wall-clock timeout (if it worked) or forever (if it didn't).

6. **No hard ceiling** — the old system had a 90-minute absolute safety net (`HARD_CEILING_SECS=5400`) that fired regardless of activity. The new system has only the single `DISPATCH_TIMEOUT_SECS` which is an idle timeout, not a ceiling.

### 1.3 Lessons from the Previous System (intel-agency/workflow-orchestration-service)

The previous system's `run_opencode_prompt.sh` implemented a battle-tested watchdog over ~6 months of production use across 4+ deployed template clones. Key learnings from 5 consecutive false-positive idle kills and the forensic reports that followed:

#### Architecture Difference

| Aspect | Old System | New System |
|--------|-----------|------------|
| Runtime | Bash script in GHA devcontainer | Python webhook-receiver in Docker |
| Client + Server | Same container | **Separate containers** (HTTP-connected) |
| `/proc/<pid>/io` | Accessible (server PID in same container) | **Not accessible** (server in different container) |
| Activity signals | Client output mtime + server `/proc/io` (read_bytes + write_bytes) | Client stderr/stdout line freshness + structured log parsing |
| Trace output | `[watchdog]` heartbeats in CI log | None |

#### What Worked (and Must Be Preserved)

1. **Dual-channel activity detection** — client output freshness alone is insufficient. During subagent delegation, the opencode client blocks silently while the server-side subagent works. The old system's `/proc/<pid>/io` monitoring caught this. The new system must find an equivalent signal.

2. **Tiered grace windows** — `write_bytes` changing = strong progress signal (full timeout reset). `read_bytes` changing (writes flat) = weaker signal (shorter grace period). Neither changing = truly idle. This prevented false kills during network-heavy work.

3. **30-second polling interval** — frequent enough to catch stalls quickly; infrequent enough to avoid overhead.

4. **Live heartbeat tracing** — `[watchdog] elapsed=886s output_idle=886s server_idle=0s write_active=true effective_idle=0s` lines in the CI log were critical for post-mortem diagnosis. Debug mode added `read_bytes`, `write_bytes`, `log_size`, `log_lines`, `pid`.

5. **Pre-termination server log dump** — last 80 lines of server log on every kill, regardless of debug mode.

6. **SIGTERM → 10s grace → SIGKILL** — graceful shutdown attempt followed by force-kill.

7. **Hard ceiling** — 90-minute absolute max, independent of activity signals.

#### What Failed (and Must Be Avoided)

1. **Single-metric monitoring** — write_bytes-only monitoring killed 5 consecutive runs during network-heavy subagent work (PR reviews, API calls) where write_bytes plateaued for >15 min.

2. **Summed read+write bytes** — background socket reads increment `read_bytes` perpetually, making idle detection impossible when summing.

3. **Falling back to log mtime when `/proc/io` unavailable** — log mtime reflected startup time, not last activity, causing false idle declarations.

4. **Activity flags not reset per iteration** — `write_active`/`read_active` evaluated once per loop but used across iterations without reset.

5. **No graceful degradation when `/proc/io` unavailable** — the watchdog should still function (with reduced accuracy) when the primary signal source is missing.

### 1.4 Available Activity Signals in the New System

The webhook-receiver spawns the opencode CLI via `subprocess.Popen` and captures its stderr/stdout via `_stream_to_logger_and_file` (`runner.py:248-265`). This function reads every line in real-time, making it the primary activity signal source.

| Signal | Source | Strength | Notes |
|--------|--------|----------|-------|
| **stderr line arrival** | `_stream_to_logger_and_file` reading `proc.stderr` | Strong | Any line = process is producing output. Equivalent to old system's client output mtime. |
| **Structured log: `message=stream`** | Orchestrator service logs on stderr | Strong | LLM stream initiation = active work |
| **Structured log: `message=loop step=N`** | Orchestrator service logs on stderr | Strong | Agent loop iteration = active work |
| **Structured log: `message=evaluated permission`** | Orchestrator service logs on stderr | Medium | Tool permission check = agent is making tool calls |
| **Structured log: `level=ERROR`** | Orchestrator service logs on stderr | Special | Error = process alive but may be stuck (e.g., rate limits) |
| **Structured log: `"exiting loop"`** | Orchestrator service logs on stderr | Terminal | Session/subagent completed |
| **Glyph lines: `⚙`, `•`, `✓`, `→`, `←`** | opencode client stderr | Strong | Tool calls, delegations, reads, writes |
| **`message=created id=ses`** | Orchestrator service logs on stderr | Medium | Subagent session created |
| **HTTP health check** | `http://orchestratorservice:4099/health` | Weak | Server is up, but says nothing about progress |

### 1.5 Proposed Design: `IdleWatchdog` Class

A dedicated watchdog thread that replaces the current `proc.wait(timeout=...)` approach with activity-aware monitoring, live tracing, and pre-termination diagnostics.

#### Configuration Constants

| Constant | Value | Source | Rationale |
|----------|-------|--------|-----------|
| `IDLE_TIMEOUT_SECS` | 900 (15 min) | Old system's proven value | Kill after no qualifying activity for 15 min |
| `ERROR_GRACE_SECS` | 300 (5 min) | New | Consecutive errors (rate limits) get shorter grace |
| `HARD_CEILING_SECS` | 5400 (90 min) | Old system's proven value | Absolute safety net regardless of activity |
| `POLL_INTERVAL_SECS` | 30 | Old system's proven value | Watchdog polling frequency |
| `MAX_CONSECUTIVE_ERRORS` | 5 | New (gap-miner had 5 before manual kill) | Abort after N consecutive `stream error` lines |
| `SIGTERM_GRACE_SECS` | 10 | Old system's proven value | Time between SIGTERM and SIGKILL |

#### Activity Signal Tracking

The `_stream_to_logger_and_file` function is modified to update a shared `WatchdogState` object on every line received:

```python
@dataclass
class WatchdogState:
    last_line_time: float          # monotonic time of last stderr/stdout line
    last_strong_signal_time: float  # last stream/loop/tool/glyph line
    last_error_time: float         # last level=ERROR line
    consecutive_errors: int        # consecutive error lines without non-error
    last_error_message: str        # most recent error text
    total_lines: int               # total lines received
    lock: threading.Lock           # protects all fields
```

Signal classification (parsed from each stderr line):
- **Strong**: `message=stream`, `message=loop step=`, `message=evaluated permission`, glyph lines (`⚙`, `•`, `✓`, `→`, `←`, `✱`), `message=created id=ses`, `message=formatting`
- **Error**: `level=ERROR` (resets `last_error_time`, increments `consecutive_errors`)
- **Neutral**: everything else (resets `last_line_time` and `consecutive_errors`, but NOT `last_strong_signal_time`)

#### Watchdog Loop Logic

```
every POLL_INTERVAL_SECS (30s):
  1. Check if process is alive (proc.poll())
  2. Compute elapsed = now - start_time
  3. Compute line_idle = now - state.last_line_time
  4. Compute strong_idle = now - state.last_strong_signal_time
  5. Compute error_idle = now - state.last_error_time
  
  # Hard ceiling (unconditional)
  if elapsed >= HARD_CEILING_SECS:
    log "[watchdog] hard ceiling reached elapsed={elapsed}s"
    kill process (SIGTERM → grace → SIGKILL)
    break
  
  # Consecutive error abort
  if state.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
    log "[watchdog] {N} consecutive errors, last: {msg}"
    kill process
    break
  
  # Idle timeout (based on strong signals, with error grace)
  effective_idle = min(line_idle, strong_idle)
  if state.consecutive_errors > 0 and error_idle < ERROR_GRACE_SECS:
    # Errors happening but within grace — use error grace window
    threshold = ERROR_GRACE_SECS
  else:
    threshold = IDLE_TIMEOUT_SECS
  
  if effective_idle >= threshold:
    log "[watchdog] idle for {effective_idle}s (threshold={threshold}s)"
    kill process
    break
  
  # Heartbeat trace
  if line_idle >= 60:
    log "[watchdog] elapsed={elapsed}s line_idle={line_idle}s strong_idle={strong_idle}s errors={consecutive_errors} lines={total_lines}"
  elif DEBUG:
    log "[watchdog] elapsed={elapsed}s line_idle={line_idle}s strong_idle={strong_idle}s errors={consecutive_errors} lines={total_lines}"
```

#### Pre-Termination Diagnostics

On any watchdog kill, dump:
1. Last 20 lines from the stderr file (recent activity)
2. Last 5 non-filtered lines from the logger (what the operator saw)
3. The watchdog state summary (elapsed, idle times, error count, line count)
4. Post a specific failure comment on the issue with classification: `idle_timeout`, `hard_ceiling`, or `consecutive_errors`

#### Termination Sequence

```python
def _terminate_process(proc, reason):
    proc.terminate()  # SIGTERM
    try:
        proc.wait(timeout=SIGTERM_GRACE_SECS)
    except subprocess.TimeoutExpired:
        proc.kill()  # SIGKILL
        proc.wait()
```

### 1.6 Implementation Tasks

| # | Task | File | Description |
|---|------|------|-------------|
| 1 | Add `WatchdogState` dataclass | `runner.py` | Thread-safe state object shared between stream reader and watchdog |
| 2 | Modify `_stream_to_logger_and_file` | `runner.py` | Update `WatchdogState` on every line; classify signal strength |
| 3 | Implement `IdleWatchdog` class | `runner.py` (new) or `watchdog.py` | Polling loop with activity detection, heartbeats, termination |
| 4 | Replace `proc.wait(timeout=...)` in `_run_completion_watcher` | `runner.py` | Watchdog owns the kill decision; watcher just waits for exit |
| 5 | Add watchdog constants to `config.py` | `config.py` | `IDLE_TIMEOUT_SECS`, `ERROR_GRACE_SECS`, `HARD_CEILING_SECS`, `POLL_INTERVAL_SECS`, `MAX_CONSECUTIVE_ERRORS` as env-configurable |
| 6 | Add pre-termination diagnostics | `runner.py` | Dump recent stderr lines + watchdog state on kill |
| 7 | Classify failure reason in manifest | `runner.py` | `idle_timeout`, `hard_ceiling`, `consecutive_errors`, `timed_out` (legacy wall-clock) |
| 8 | Add unit tests | `tests/test_watchdog.py` | Test signal classification, idle computation, consecutive error counting, hard ceiling |
| 9 | Add integration test | `tests/test_runner.py` | Mock process that goes idle → verify watchdog kills and posts correct comment |

### 1.7 Migration Path

The current `DISPATCH_TIMEOUT_SECS` env var is kept as the hard ceiling value (renamed semantically to `HARD_CEILING_SECS` but the old env var name still works for backward compat). New env vars:

| Env Var | Default | Description |
|---------|---------|-------------|
| `IDLE_TIMEOUT_SECS` | 900 | Kill after this many seconds of no strong activity signals |
| `ERROR_GRACE_SECS` | 300 | Grace period for consecutive errors before abort |
| `HARD_CEILING_SECS` | `${DISPATCH_TIMEOUT_SECS:-5400}` | Absolute maximum runtime |
| `WATCHDOG_POLL_SECS` | 30 | Polling interval |
| `MAX_CONSECUTIVE_ERRORS` | 5 | Abort after N consecutive error lines |
| `WATCHDOG_DEBUG` | `false` | Emit heartbeats even when process is active |

---

## Issue 2: No Milestone, Project, or PR Attached to Dispatch Issue

### Evidence

- Issue #1 is the dispatch trigger (labeled `orchestration:dispatch`)
- The orchestrator created Issue #3 (Application Plan) and linked it to Project #70 and Milestone #1 (`gha_workflow_output.log:293-316`)
- Issue #1 received progress comments (line 441) but was never linked to a milestone, project, or PR
- The workflow's final step ("Publish & verify: push branch, ensure PR exists, close issue #1") was never reached — the run died on API limits at assignment 3/6

### Root Cause

This is a **workflow design gap**, not a code bug. The orchestrator prompt's workflow assigns milestones/projects only to the Application Plan issue (#3), not to the dispatch issue (#1). The dispatch issue is only closed at the very end of a successful run ("close issue #1"). If the run fails mid-way, issue #1 is left orphaned with:
- No milestone (so it doesn't appear in milestone views)
- No project linkage (so it doesn't appear in the project board)
- No PR (so there's no code trail)
- Progress comments but no structured metadata

### Solution Options

| Option | Pros | Cons |
|--------|------|------|
| **A. Link dispatch issue to project/milestone at dispatch time** — in the `orchestration:dispatch` prompt clause, add project/milestone linkage immediately | Issue #1 is always tracked even if run fails mid-way | Requires knowing the milestone at dispatch time (may not exist yet) |
| **B. Link in the runner** — after dispatch, use `gh` CLI to add the issue to the project board | Decoupled from the LLM prompt; always runs | Runner doesn't know which project/milestone to use |
| **C. Add to the orchestrator prompt's pre-script-begin step** — link issue #1 to project #70 before any assignments run | Early linkage; uses existing `gh` CLI context | Prompt change only; still LLM-dependent |

**Recommendation: C.** Add a step in the orchestrator prompt's pre-script-begin section to link the dispatch issue to the project board immediately. This is the earliest point where the orchestrator has context about the repo and can run `gh project item-add`. It's prompt-level, so no code changes needed.

---

## Issue 3: Model API Limit Reached Error Not Handled Gracefully

### Evidence

```
08:39:14.784 ERROR "stream error" error="AI_APICallError: Usage limit reached for 5 hour. Your limit will reset at 2026-07-11 20:11:13"
08:39:17.676 ERROR "stream error" (retry after ~2s)
08:39:22.689 ERROR "stream error" (retry after ~5s)
08:39:31.718 ERROR "stream error" (retry after ~9s)
08:39:48.809 ERROR "stream error" (retry after ~17s)
```

`gha_workflow_output.log:2359-2371` — 5 consecutive rate-limit errors on session `ses_0afac2232ffeMvX3SAN0l6pZ9N` (developer agent, model `glm-5.1`).

### Root Cause

The retry behavior is **inside the opencode orchestrator service** (the `orchestratorservice-1` container), not in the webhook-receiver. The orchestrator service uses `ai-sdk` runtime (`gha_workflow_output.log:2358`) which has built-in retry with exponential backoff. However:

1. **No model fallback**: When `glm-5.1` hits its 5-hour rate limit, the system has no fallback to another model (e.g., `glm-5`, openrouter, or gemini). The orchestrator service is configured with a single model per agent.

2. **No graceful abort**: The retry loop continues indefinitely (or until the process is killed). There's no "give up after N retries" or "wait until reset time" logic. The error message even tells us the reset time (`2026-07-11 20:11:13` — ~11.5 hours away), but nothing parses or acts on this.

3. **No webhook-receiver visibility**: The webhook-receiver's runner sees the opencode process's stderr but doesn't parse `stream error` lines for rate-limit detection. The `_run_completion_watcher` only classifies runs by exit code and tool usage — it has no mid-run error detection.

4. **Wasted compute**: The developer agent burned ~35 seconds of retry loops before the user manually killed it. In a headless run, this would continue until the DISPATCH_TIMEOUT (if it works) or indefinitely.

### Solution Options

| Option | Pros | Cons |
|--------|------|------|
| **A. Model fallback in orchestrator service** — configure a secondary model per agent; on rate-limit, switch | Seamless recovery; no human needed | Requires orchestrator service changes (upstream); complex |
| **B. Rate-limit-aware retry in orchestrator** — parse the reset time, sleep until reset or abort after N retries | Prevents wasted compute; clean abort | Requires orchestrator service changes (upstream) |
| **C. Webhook-receiver mid-run error detection** — scan stderr for `stream error` + `Usage limit`, kill the process after N consecutive errors | Actionable at the webhook-receiver level; no upstream changes needed | Kills the entire run, not just the failing session; blunt instrument |
| **D. Prompt-level instruction** — add to the orchestrator prompt: "if you encounter API rate limits, post a comment and exit gracefully" | Zero code changes; works within existing infrastructure | LLM-dependent; unreliable under error conditions |

**Recommendation: C (short-term) + A (long-term).** 

Short-term: Add a stderr monitor in the webhook-receiver that counts consecutive `stream error` lines with `Usage limit` and kills the process after 3+ consecutive errors, posting a specific "API rate limit" failure comment (distinct from generic timeout/failure). This is implementable in `runner.py`'s `_stream_to_logger_and_file` or a new monitor thread.

Long-term: The orchestrator service needs model fallback configuration. This is an upstream change to the opencode/orchestrator-service image.

---

## Issue 4: Server Trace Output Filters Not Hiding Specified Patterns

### Evidence

The 4 example lines from `issues.md:25-28`:

| Line | Pattern | Blacklist Match |
|------|---------|-----------------|
| `message=tracking hash=a1c30f...` | `message=tracking hash=` | **Pattern #2** (`filters.py:16`) |
| `message=process session.id=...messageID=...` | `message=process .*messageID=` | **Pattern #6** (`filters.py:20`) |
| `message=stream providerID=...modelID=...` | `message=stream .*modelID=` | **Pattern #4** (`filters.py:18`) |
| `"llm runtime selected"` | `"llm runtime selected"` | **Pattern #5** (`filters.py:19`) |

All 4 lines match existing blacklist patterns, yet they appear in the container output.

### Root Cause

**The filters apply to the wrong log stream.** The architecture has two containers:

1. **`orchestratorservice-1`** — the opencode server. Emits structured logs (`timestamp=... level=INFO run=... message=tracking hash=...`). These are the lines in question.

2. **`webhook-receiver-1`** — the Python webhook receiver. Runs the opencode CLI as a subprocess and captures its stderr via `_stream_to_logger_and_file` (`runner.py:248-265`). The `should_filter()` call at line 262 suppresses blacklisted lines from the **webhook-receiver's logger output** (the `[opencode]` and `[opencode-err]` prefixed lines).

The `orchestratorservice-1` container has its **own** logging pipeline. It writes directly to its stdout/stderr, which Docker captures as container logs. The webhook-receiver's `should_filter()` has **zero effect** on the orchestrator service's output. The two containers are peers connected by HTTP (`http://orchestratorservice:4099`), not by a shared log pipe.

The lines in `issues.md:25-28` are prefixed with `orchestratorservice-1 |`, confirming they come from the orchestrator service container, not the webhook-receiver.

### Solution Options

| Option | Pros | Cons |
|--------|------|------|
| **A. Docker log driver filtering** — use a custom log driver or `docker compose` log opts to filter patterns | Infrastructure-level; applies to all container output | Docker log drivers don't support regex filtering; would need a log proxy |
| **B. Log proxy sidecar** — add a sidecar container that tails orchestrator logs and re-emits filtered output | Full control over filtering; works for any log consumer | Additional container; adds latency and complexity |
| **C. Accept the duplication** — the filters work correctly for the webhook-receiver's captured stderr (the `[opencode-err]` lines); the orchestrator service logs are a separate concern | No changes needed; correct understanding of the architecture | Container logs remain noisy |
| **D. Orchestrator service log-level config** — set the orchestrator service to emit only WARN+ERROR, suppressing the INFO-level boilerplate at the source | Eliminates noise at the source; no filtering needed | May lose useful INFO lines (e.g., `exiting loop`, permission denials); requires orchestrator service config change |

**Recommendation: C (understanding) + D (if noise is unacceptable).**

The filters are working as designed — they filter the webhook-receiver's capture of the opencode CLI stderr, not the orchestrator service's own logs. This is the correct architecture per the project decision (`log_filtering_architecture`: "runner.py writes every line to trace files for full fidelity; should_filter() suppresses boilerplate only from the container logger").

If the orchestrator service's container logs are too noisy, the fix is at the orchestrator service level (reduce log verbosity or add server-side filtering), not in the webhook-receiver. The `docker compose logs` output will always show both containers' raw output.

---

## Summary

| Issue | Severity | Type | Fix Location |
|-------|----------|------|-------------|
| 1. Idle timeout watchdog redesign | **High** | Architectural gap (wall-clock only, no activity detection) | `runner.py` — new `IdleWatchdog` + `WatchdogState` |
| 2. No milestone/project on dispatch issue | **Medium** | Workflow gap | Orchestrator prompt (pre-script-begin) |
| 3. API rate limit not handled | **High** → **Subsumed by #1** | The watchdog's `MAX_CONSECUTIVE_ERRORS` abort handles this | `runner.py` (part of watchdog design) |
| 4. Trace filters not hiding patterns | **Low** | Misunderstanding (filters work correctly on webhook-receiver logs) | No fix needed |

**Issue 3 is subsumed by Issue 1.** The watchdog's consecutive-error detection (`MAX_CONSECUTIVE_ERRORS=5`) directly handles the gap-miner's rate-limit scenario: 5 consecutive `AI_APICallError: Usage limit reached` lines trigger an abort with a specific "API rate limit" failure comment, eliminating the need for a separate rate-limit monitor.

## Recommended Implementation Order

1. **Issue 1 (+ Issue 3)** — Implement the `IdleWatchdog` with `WatchdogState`, activity-aware idle detection, consecutive error abort, hard ceiling, live heartbeats, and pre-termination diagnostics. This is the highest-impact change and subsumes the rate-limit handling gap.
2. **Issue 2** — Update the orchestrator prompt to link the dispatch issue to the project board at pre-script-begin. Prompt-only change, no code.
3. **Issue 4** — Document the architecture (filters apply to webhook-receiver logs only). No code change needed.

## Open Questions

1. **Should the watchdog's `IDLE_TIMEOUT_SECS` (15 min) be shorter than the old system's value?** The old system's 15-min idle timeout was tuned for GHA runners where subagent delegation could block client output for extended periods. In the Docker-based system, the opencode CLI streams orchestrator service logs to stderr continuously (including during subagent work), so `line_idle` should rarely exceed a few seconds during active work. 15 min may be conservative — 10 min could be safe. **Recommendation: start at 900s (proven value), tune down after observing real runs.**

2. **Should the watchdog monitor the orchestrator service's HTTP health endpoint?** Polling `http://orchestratorservice:4099/health` would detect server crashes that the CLI stderr stream wouldn't catch (e.g., OOM kill of the server container). However, this is a weak signal (server up ≠ making progress) and adds a dependency. **Recommendation: defer to a follow-up; the stderr stream already captures `stream error` lines when the server is unhealthy.**

3. **Should `DISPATCH_TIMEOUT_SECS` be deprecated in favor of `HARD_CEILING_SECS`?** The current env var name implies a wall-clock timeout, which is exactly what we're moving away from. Renaming to `HARD_CEILING_SECS` is semantically correct but breaks backward compat. **Recommendation: accept both names, with `HARD_CEILING_SECS` taking precedence and `DISPATCH_TIMEOUT_SECS` as fallback. Log a deprecation warning when the old name is used.**
