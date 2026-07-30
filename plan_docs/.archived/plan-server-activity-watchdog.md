# Implementation Plan: Server-Side Activity Watchdog

> **Origin:** `docs/orchestration-failure-report-2026-07-22-golf86-issue1.md` §9.5
> **Problem:** The watchdog kills the opencode CLIENT on a false-positive idle signal during subagent execution, but the opencode SERVER continues the agent session to completion. The kill is both incorrect (false positive) and ineffective (no-op for server-side work).
> **Goal:** Make the watchdog see server-side activity so it never fires prematurely, and make the kill effective when a genuine stall does occur.

---

## Architecture Context

```
┌─────────────────────────────────┐     ┌──────────────────────────────────────┐
│  webhook-receiver container     │     │  orchestratorservice container       │
│                                 │     │                                      │
│  runner.py                      │     │  opencode serve (pid 1)              │
│    ├─ Popen("opencode run       │────▶│    ├─ orchestrator session           │
│    │   --attach ...")  pid=156  │ HTTP│    │   └─ subagent sessions           │
│    ├─ stdout/stderr reader ─────┼─threads                                   │
│    ├─ IdleWatchdog (watchdog.py)│     │    └─ /home/app/.local/share/        │
│    │   └─ monitors stdout ONLY  │     │       opencode/log/opencode.log     │
│    └─ _terminate() → kill(156)  │     │         ↑ ACTIVE during subagent    │
│                                 │     │                                      │
└─────────────────────────────────┘     └──────────────────────────────────────┘
         shared: /workspace                       NOT shared: server log ← FIX THIS
```

The server log at `/home/app/.local/share/opencode/log/opencode.log` contains real-time agent activity (LLM streaming, tool calls, session loop steps) — confirmed active during subagent execution. It is currently inaccessible from the webhook-receiver container.

---

## Work Items

### WI-1: Immediate stopgap — bump idle timeout (5 min, zero risk)

**Files:** `compose.yaml`

Change the default `IDLE_TIMEOUT_SECS` from 900 (15 min) to 1800 (30 min). This is the same recommendation from the predecessor system's forensic report. It prevents the false-positive kill while the structural fix (WI-2/WI-3) is being built.

```yaml
# compose.yaml, webhook-receiver environment:
- IDLE_TIMEOUT_SECS=${IDLE_TIMEOUT_SECS:-1800}   # was 900
```

**Verify:** `docker compose up -d webhook-receiver`, trigger a direct-body dispatch, confirm the watchdog heartbeat shows `idle_timeout=1800s`.

---

### WI-2: Share the server log volume (15 min, low risk)

**Files:** `compose.yaml`

Add a named volume for the opencode log directory, mounted read-only in the webhook-receiver.

```yaml
services:
  orchestratorservice:
    volumes:
      - opencode-memory:/app/.memory
      - ${WORKSPACE_DIR:?}:/workspace
      - opencode-logs:/home/app/.local/share/opencode/log    # ADD

  webhook-receiver:
    volumes:
      - ${WORKSPACE_DIR:?}:/workspace
      - ${WEBHOOK_LOG_DIR:-./traces/runner}:/tmp/orchestrator-webhook
      - opencode-logs:/home/app/.local/share/opencode/log:ro  # ADD

volumes:
  opencode-memory:
  opencode-logs:    # ADD
  caddy_data:
  caddy_config:
```

**Verify:**
```bash
docker compose down && docker compose up -d
# From webhook-receiver container:
docker exec orchestrator-service-webhook-receiver-1 \
  ls -la /home/app/.local/share/opencode/log/opencode.log
# Should show the file (read-only)
docker exec orchestrator-service-webhook-receiver-1 \
  tail -5 /home/app/.local/share/opencode/log/opencode.log
```

**Risk:** If the server log path changes between opencode versions, the mount breaks silently (file not found → watchdog falls back to client-only signal). This is safe degradation — same as no fix.

---

### WI-3: Add server-log mtime activity signal to watchdog (30 min, root fix)

**Files:** `webhook_receiver/watchdog.py`, `webhook_receiver/config.py`

This is the core fix. Add a server-log-mtime check to the idle decision so the watchdog sees server activity and withholds the kill.

#### 3a. Add config fields

`webhook_receiver/config.py` — add to `Settings` dataclass and `from_env()`:

```python
# In Settings dataclass (add near idle_timeout_secs):
server_log_path: str = "/home/app/.local/share/opencode/log/opencode.log"

# In from_env(), add:
server_log_path=os.environ.get(
    "OPENCODE_SERVER_LOG_PATH",
    "/home/app/.local/share/opencode/log/opencode.log",
),
```

`webhook_receiver/watchdog.py` — add to `WatchdogConfig` and `from_settings()`:

```python
@dataclass(frozen=True)
class WatchdogConfig:
    idle_timeout_secs: int = 900
    error_grace_secs: int = 300
    hard_ceiling_secs: int | None = 5400
    poll_interval_secs: int = 30
    max_consecutive_errors: int = 5
    sigterm_grace_secs: int = 10
    debug: bool = False
    server_log_path: str = "/home/app/.local/share/opencode/log/opencode.log"  # ADD

    @classmethod
    def from_settings(cls, settings: object) -> WatchdogConfig:
        return cls(
            idle_timeout_secs=getattr(settings, "idle_timeout_secs", 900),
            error_grace_secs=getattr(settings, "error_grace_secs", 300),
            hard_ceiling_secs=getattr(settings, "hard_ceiling_secs", 5400),
            poll_interval_secs=getattr(settings, "watchdog_poll_secs", 30),
            max_consecutive_errors=getattr(settings, "max_consecutive_errors", 5),
            debug=getattr(settings, "watchdog_debug", False),
            server_log_path=getattr(                                     # ADD
                settings, "server_log_path",
                "/home/app/.local/share/opencode/log/opencode.log",
            ),
        )
```

#### 3b. Add server-log idle calculation to `IdleWatchdog.run()`

In `watchdog.py`, inside the `run()` method's main loop (after `line_idle` calculation, before the idle-timeout check at line 336), add:

```python
# ── Server-log mtime activity signal ──────────────────────────
# The opencode server (separate container) writes structured log
# entries during agent/subagent execution. If the client stdout is
# silent (blocked on subagent) but the server log is being written,
# the agent is actively working — withhold the kill.
server_log = Path(self._config.server_log_path)
server_log_idle: float | None = None
if server_log.exists():
    try:
        server_log_idle = now - server_log.stat().st_mtime
    except OSError:
        server_log_idle = None

# Effective idle = the MINIMUM of client idle and server-log idle.
# If either signal shows recent activity, the process is not idle.
if server_log_idle is not None:
    effective_idle = min(line_idle, server_log_idle)
else:
    effective_idle = line_idle  # fallback to client-only (current behavior)
```

Then change the idle-timeout check to use `effective_idle` instead of `line_idle`:

```python
# Was: if line_idle >= cfg.idle_timeout_secs:
if effective_idle >= cfg.idle_timeout_secs:
```

And update the heartbeat trace to include server-log info:

```python
# In the heartbeat log line (line ~363), add:
if server_log_idle is not None:
    logger.info(
        "[watchdog] elapsed=%ds line_idle=%ds server_log_idle=%ds "
        "effective_idle=%ds errors=%d/%d lines=%d pid=%s",
        int(elapsed), int(line_idle), int(server_log_idle),
        int(effective_idle),
        snap.consecutive_errors, cfg.max_consecutive_errors,
        snap.total_lines, self._proc.pid,
    )
else:
    # existing heartbeat (server log not available)
    logger.info(...)
```

#### 3c. Tests

Add test cases to the existing watchdog test suite:

- `test_server_log_active_withholds_kill` — server log mtime < idle_timeout, client idle → no kill
- `test_server_log_stale_allows_kill` — server log mtime > idle_timeout, client idle → kill fires
- `test_server_log_missing_falls_back_to_client` — server log path doesn't exist → behaves as before (client-only)
- `test_both_active_no_kill` — both client and server log active → no kill

**Verify:**
```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -Test    # run test gate
# Then trigger a real dispatch and watch for:
# [watchdog] elapsed=300s line_idle=200s server_log_idle=5s effective_idle=5s ...
# (effective_idle should be low when server is active, preventing the kill)
```

---

### WI-4: Process-group kill in `_terminate()` (15 min, defense in depth)

**Files:** `webhook_receiver/watchdog.py`

Change `_terminate()` to kill the entire process group, not just the direct child PID. Since `start_new_session=True` (runner.py:719) makes the child a session leader, `os.getpgid(proc.pid)` returns the child's PID as the PGID.

```python
import os
import signal

def _terminate(self, reason: str) -> int:
    """SIGTERM → grace → SIGKILL escalation on the entire process group."""
    proc = self._proc
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = proc.pid  # already gone — try direct PID as fallback

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        proc.wait(timeout=self._config.sigterm_grace_secs)
    except subprocess.TimeoutExpired:
        logger.warning(
            "[watchdog] process group did not exit after SIGTERM "
            "(grace=%ds), sending SIGKILL",
            self._config.sigterm_grace_secs,
        )
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    return proc.returncode if proc.returncode is not None else -15
```

**Verify:** Trigger a dispatch, manually send SIGSTOP to the client process (simulating a stall), wait for the watchdog to kill it, confirm no orphaned child processes remain: `ps aux | grep opencode` in both containers.

---

### WI-5: Server-side session abort on genuine kill (research + implement, 1-2 hrs)

**Files:** `webhook_receiver/runner.py`, `webhook_receiver/watchdog.py`

When the watchdog kills a run for a **genuine** stall (both client AND server log are idle), the server-side agent session may still be alive on the `orchestratorservice` container. It should be explicitly aborted to stop wasted API calls and prevent the "server completes after client kill" scenario.

#### Research needed

1. **Does `opencode session delete <sessionID>` abort an active session, or just delete stored data?** Test by starting a long run, then deleting the session from another shell.
2. **Does the opencode server HTTP API have an abort/cancel endpoint?** Probe `http://orchestratorservice:4099/session/<id>` with DELETE method.
3. **Does killing the client cleanly signal the server to abort?** The `--attach` protocol may send a disconnect/abort message on clean SIGTERM (vs SIGKILL). Test by checking server logs after a SIGTERM-only kill.

#### Implementation (after research)

If `opencode session delete` aborts active sessions:

```python
# In _run_completion_watcher, after the watchdog kills the process:
if kill_reason in (REASON_IDLE_TIMEOUT, REASON_HARD_CEILING, REASON_CONSECUTIVE_ERRORS):
    # Extract the session ID from the server log (parse "session.id=ses_..." lines)
    session_id = _extract_session_id(stderr_path)
    if session_id:
        _abort_server_session(session_id, settings.opencode_server_url)
```

```python
def _abort_server_session(session_id: str, server_url: str) -> None:
    """Abort a server-side agent session after a watchdog kill."""
    try:
        subprocess.run(
            ["opencode", "session", "delete", session_id,
             "--attach", server_url],
            timeout=30,
            capture_output=True,
        )
        logger.info("[watchdog] aborted server session %s", session_id)
    except Exception:
        logger.warning("[watchdog] failed to abort server session %s", session_id)
```

If the HTTP API supports it, use `requests.delete(f"{server_url}/session/{session_id}")` instead.

**Verify:** Start a long-running dispatch, force-kill the client, confirm the server-side session is aborted (no further log entries in `opencode.log`, no further GitHub API calls).

---

### WI-6: Post-kill reconciliation comment (30 min, quality improvement)

**Files:** `webhook_receiver/runner.py`

When the watchdog kills a run but the server-side session later completes successfully (posts comments, closes issues), the failure comment on the triggering issue is misleading. Add a reconciliation check:

After the watchdog kill, start a delayed background check (e.g., 5 min later) that queries the GitHub issue for new comments or state changes posted after the kill timestamp. If found, post a follow-up comment correcting the failure:

```
ℹ️ Update: although the orchestrator client was terminated by the idle
watchdog at <time>, the server-side agent session completed successfully
and posted <N> additional comments / closed this issue at <time>.
```

This prevents contradictory "❌ failed" + "✅ completed" comment pairs.

**Verify:** Reproduce the golf86 scenario, confirm the reconciliation comment appears after the server-side session completes.

---

## Implementation Order

| Phase | Work Items | Effort | Outcome |
|---|---|---|---|
| **Phase 0** (now) | WI-1 | 5 min | Immediate unblock — false-positive kills become much less likely |
| **Phase 1** (this PR) | WI-2 + WI-3 + WI-4 | ~1 hr | Root fix — watchdog sees server activity; kills target process group |
| **Phase 2** (next PR) | WI-5 | 1-2 hrs | Server-side session abort — kills are actually effective |
| **Phase 3** (follow-up) | WI-6 | 30 min | Reconciliation — no misleading failure comments |

Phase 0 can be deployed immediately with a single `compose.yaml` edit + container restart. Phases 1–2 are the structural fix.

---

## File Change Summary

| File | WI | Changes |
|---|---|---|
| `compose.yaml` | 1, 2 | `IDLE_TIMEOUT_SECS` default 900→1800; add `opencode-logs` volume |
| `webhook_receiver/config.py` | 3 | Add `server_log_path` field to `Settings` + `from_env()` |
| `webhook_receiver/watchdog.py` | 3, 4 | Add `server_log_path` to `WatchdogConfig`; add server-log-mtime idle signal in `run()`; replace `_terminate()` with process-group kill |
| `webhook_receiver/runner.py` | 5, 6 | Server-side session abort on kill; post-kill reconciliation comment |
| `tests/test_watchdog.py` (or similar) | 3 | Test cases for server-log active/stale/missing scenarios |

---

## Verification Checklist

- [ ] `compose.yaml` — `IDLE_TIMEOUT_SECS` shows 1800 in container env
- [ ] Server log file accessible from webhook-receiver container (`ls` + `tail`)
- [ ] Watchdog heartbeat shows `server_log_idle` field
- [ ] Dispatch with subagent delegation survives past 15 min (no false kill)
- [ ] Watchdog heartbeat shows `effective_idle` dropping when server log is active
- [ ] Genuine stall (SIGSTOP client) still triggers kill after idle timeout
- [ ] Kill targets process group — no orphaned processes after termination
- [ ] `pwsh -NoProfile -File ./scripts/validate.ps1 -All` passes (lint + test gate)
- [ ] Server-side session abort works (if WI-5 implemented)
- [ ] Reconciliation comment appears after server-side completion (if WI-6 implemented)
