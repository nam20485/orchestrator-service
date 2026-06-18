# Issues Implementation Plan

Based on `docs/issues.md`. Each issue rated by **Complexity** (1-10) vs **Value** (1-10), grouped by efficiency ratio (value/complexity), and sorted within groups by dependency order.

**Final destination:** This plan should be saved to `docs/issues-implementation-plan.md` for repo visibility.

## Summary Ratings

| Issue | Description | Complexity | Value | Ratio | Group |
|-------|-------------|-----------|-------|-------|-------|
| I6 | Compose `OPENCODE_SERVER_PASSWORD` enforcement | 1 | 5 | 5.0 | A — Quick Wins |
| I2 | Print prompt output from webhook receiver | 2 | 8 | 4.0 | A — Quick Wins |
| I3 | Webhook event lifecycle tracing | 2 | 7 | 3.5 | A — Quick Wins |
| I1 | Trace filtering (small blacklist) | 3 | 7 | 2.3 | A — Quick Wins |
| I7 | Client script env var URL resolution | 3 | 5 | 1.7 | A — Quick Wins |
| I5 | Idle timeout for orchestration runs | 7 | 9 | 1.3 | B — Major Work |
| I4 | K8s IaC deployment resources | 9 | 5 | 0.6 | C — Deferred |

**Ratio** = Value / Complexity. Higher is better (more value per unit of effort).

## Dependency Graph

```
I6 (independent)
I2 ──→ I1 (filtering applies to the output that I2 enables)
I3 (independent, but complements I2)
I7 (independent)
I5 ──→ I2 (timeout monitors the stdout that I2 captures)
I4 (independent, large scope)
```

**Recommended execution order:** I6 → I2 → I3 → I1 → I7 → I5 → (I4 deferred)

---

## Group A — Quick Wins

### I6 — Compose `OPENCODE_SERVER_PASSWORD` enforcement

**Complexity:** 1/10 | **Value:** 5/10 | **Files:** `compose.yaml`, `test/test-compose-config.sh`

**Current state:** `compose.yaml:23` passes `OPENCODE_SERVER_PASSWORD` through without enforcement. `docker compose up` silently starts with an empty password.

**Implementation:**

1. In `compose.yaml`, change line 23:

   ```yaml
   # Before:
   - OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD}
   # After:
   - OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD is required}
   ```

2. Also change line 38 (webhook-receiver also passes this through):

   ```yaml
   - OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD is required}
   ```

3. Update `test/test-compose-config.sh` — it already sets `OPENCODE_SERVER_PASSWORD` so it should continue to pass. Add a negative test case that verifies compose config fails without the variable:

   ```bash
   # Verify enforcement: should fail without OPENCODE_SERVER_PASSWORD
   (unset OPENCODE_SERVER_PASSWORD && docker compose config --quiet 2>/dev/null) && \
     echo "FAIL: compose should require OPENCODE_SERVER_PASSWORD" && exit 1 || \
     echo "compose enforces OPENCODE_SERVER_PASSWORD: ok"
   ```

**Validation:**

- `OPENCODE_SERVER_PASSWORD= docker compose config` → fails with clear error
- `test/test-compose-config.sh` still passes

---

### I2 — Print prompt output from webhook receiver

**Complexity:** 2/10 | **Value:** 8/10 | **Files:** `webhook_receiver/runner.py`

**Current state:** `runner.py:68` sends `stdout=subprocess.DEVNULL`. All `opencode run` output is silently discarded. When prompting manually via `prompt.ps1`, significant output is visible; the webhook receiver shows none.

**Implementation:**

1. In `runner.py`, change `dispatch_to_opencode` to capture stdout and stream it to the logger via a background thread:

   ```python
   import threading
   
   def _stream_to_logger(pipe, label: str):
       """Read lines from pipe and log them."""
       try:
           for line in iter(pipe.readline, ""):
               if line:
                   logger.info("[%s] %s", label, line.rstrip())
       except ValueError:
           pass  # pipe closed
   
   # In dispatch_to_opencode:
   proc = subprocess.Popen(
       cmd,
       stdout=subprocess.PIPE,
       stderr=subprocess.PIPE,
       start_new_session=True,
       text=True,
   )
   threading.Thread(
       target=_stream_to_logger, args=(proc.stdout, "opencode"), daemon=True
   ).start()
   threading.Thread(
       target=_stream_to_logger, args=(proc.stderr, "opencode-err"), daemon=True
   ).start()
   logger.info(
       "Started orchestration run pid=%s",
       proc.pid,
   )
   ```

2. Also write stdout/stderr to files for post-mortem analysis (keep the file-based approach alongside streaming).

**Validation:**

- Trigger a webhook event → container logs show `opencode run` output in real-time
- Check `/tmp/orchestrator-webhook/<prompt-stem>.stdout` contains full output

---

### I3 — Webhook event lifecycle tracing

**Complexity:** 2/10 | **Value:** 7/10 | **Files:** `webhook_receiver/app.py`, `webhook_receiver/prompts.py`, `webhook_receiver/runner.py`

**Current state:** `app.py` logs accepted/ignored/rejected events at INFO level with delivery_id, event, and action. Missing: payload summary, assembled prompt content, dispatch details.

**Implementation:**

1. In `app.py`, after payload parsing, add payload summary logging:

   ```python
   logger.info(
       "Webhook received delivery_id=%s event=%s action=%s repo=%s sender=%s",
       delivery_id, event,
       payload.get("action"),
       payload.get("repository", {}).get("full_name", "?"),
       payload.get("sender", {}).get("login", "?"),
   )
   ```

2. In `app.py`, after prompt assembly, log prompt metadata:

   ```python
   logger.info(
       "Prompt assembled delivery_id=%s prompt_chars=%d prompt_lines=%d",
       delivery_id, len(prompt), prompt.count("\n"),
   )
   ```

3. Optionally log the first 500 chars of the prompt at DEBUG level:

   ```python
   logger.debug("Prompt preview delivery_id=%s:\n%s", delivery_id, prompt[:500])
   ```

4. In `runner.py`, log the full command invocation at DEBUG level:

   ```python
   logger.debug("Dispatch command: %s", " ".join(cmd))
   ```

5. In `app.py`, log the raw body size and event headers at DEBUG level for diagnostics:

   ```python
   logger.debug(
       "Webhook headers delivery_id=%s: content-length=%s content-type=%s",
       delivery_id,
       request.headers.get("content-length"),
       request.headers.get("content-type"),
   )
   ```

**Validation:**

- Set `WEBHOOK_LOG_LEVEL=debug` → full lifecycle visible in container logs
- Trigger a webhook → see: received → payload summary → prompt assembled → dispatched → output streaming (from I2)

---

### I1 — Trace filtering (small blacklist)

**Complexity:** 3/10 | **Value:** 7/10 | **Files:** `webhook_receiver/runner.py` (or new `webhook_receiver/filters.py`)

**Current state:** The original project's `run_opencode_prompt.sh:266` has a massive `_SERVER_LOG_NOISE` regex with ~20 patterns that strips almost all server log output. The issue requests a **small blacklist** starting with only the noisiest patterns, extensible over time.

**Reference — original project's over-aggressive blacklist (20 patterns):**

```
service=bus | service=tool.registry | service=permission | service=bash-tool |
service=provider | service=lsp | service=file.time | service=snapshot |
cwd=.*tracking | service=session.processor | service=session.compaction |
service=session.prompt status= | service=format | service=vcs | service=storage |
ruleset=[{"permission | action={"permission | mcp stderr: .*running on |
service=llm .*stream$ | session.prompt step=.*loop$ | mcp stderr:\s*$
```

**Implementation:**

1. Create `webhook_receiver/filters.py` with a minimal, configurable blacklist:

   ```python
   import re
   import os
   
   _DEFAULT_BLACKLIST = [
       r"service=bus\s+type=message\.part\.delta",
       r"service=bus\s+type=message\.part\.updated",
   ]
   
   def _load_patterns() -> list[re.Pattern]:
       raw = os.environ.get("TRACE_BLACKLIST_PATTERNS", "")
       patterns = [p.strip() for p in raw.split("\n") if p.strip()] if raw else _DEFAULT_BLACKLIST
       return [re.compile(p) for p in patterns]
   
   _PATTERNS = _load_patterns()
   
   def should_filter(line: str) -> bool:
       return any(p.search(line) for p in _PATTERNS)
   ```

2. In `runner.py`, integrate the filter into the stdout streaming thread (from I2):

   ```python
   from webhook_receiver.filters import should_filter
   
   def _stream_to_logger(pipe, label: str):
       try:
           for line in iter(pipe.readline, ""):
               if line and not should_filter(line):
                   logger.info("[%s] %s", label, line.rstrip())
       except ValueError:
           pass
   ```

3. Make the blacklist configurable via `TRACE_BLACKLIST_PATTERNS` env var (newline-separated regex patterns). If unset, use the default minimal list.

4. Add `TRACE_BLACKLIST_PATTERNS` to compose.yaml environment (optional, commented out):

   ```yaml
   # - TRACE_BLACKLIST_PATTERNS=${TRACE_BLACKLIST_PATTERNS:-}
   ```

**Validation:**

- Trigger a run that produces `service=bus type=message.part.delta` lines → they are filtered from container logs
- All other lines (LLM calls, tool invocations, errors) pass through
- Add a pattern to `TRACE_BLACKLIST_PATTERNS` env var → verify it's filtered

---

### I7 — Client script env var URL resolution

**Complexity:** 3/10 | **Value:** 5/10 | **Files:** `scripts/prompt.ps1`, `scripts/attach.ps1`

**Current state:**

- `prompt.ps1:7` — `$ServerUrl` defaults to hardcoded `"http://localhost:4099"`
- `attach.ps1:3` — URL entirely hardcoded to `http://localhost:4099`

**Implementation:**

1. In `prompt.ps1`, replace the hardcoded default with env var resolution:

   ```powershell
   [CmdletBinding()]
   param (
       [Parameter()]
       [String]
       $ServerUrl,
       # ... other params unchanged ...
   )
   
   if (-not $ServerUrl) {
       if ($env:OPENCODE_SERVER_URL) {
           $ServerUrl = $env:OPENCODE_SERVER_URL
       } elseif ($env:OPENCODE_HOST -or $env:OPENCODE_PORT) {
           $host_ = if ($env:OPENCODE_HOST) { $env:OPENCODE_HOST } else { "localhost" }
           $port_ = if ($env:OPENCODE_PORT) { $env:OPENCODE_PORT } else { "4099" }
           $ServerUrl = "http://${host_}:${port_}"
       } else {
           $ServerUrl = "http://localhost:4099"
       }
   }
   ```

2. In `attach.ps1`, add the same resolution logic:

   ```powershell
   #! /usr/bin/env pwsh
   
   if ($env:OPENCODE_SERVER_URL) {
       $ServerUrl = $env:OPENCODE_SERVER_URL
   } elseif ($env:OPENCODE_HOST -or $env:OPENCODE_PORT) {
       $host_ = if ($env:OPENCODE_HOST) { $env:OPENCODE_HOST } else { "localhost" }
       $port_ = if ($env:OPENCODE_PORT) { $env:OPENCODE_PORT } else { "4099" }
       $ServerUrl = "http://${host_}:${port_}"
   } else {
       $ServerUrl = "http://localhost:4099"
   }
   
   opencode attach $ServerUrl --log-level INFO --print-logs --dir /workspace
   ```

**Validation:**

- `$env:OPENCODE_SERVER_URL = "http://custom:9999"; ./scripts/prompt.ps1 -Prompt "test"` → uses `http://custom:9999`
- `$env:OPENCODE_HOST = "myhost"; $env:OPENCODE_PORT = "5000"; ./scripts/attach.ps1` → connects to `http://myhost:5000`
- No env vars set → defaults to `http://localhost:4099`

---

## Group B — Major Work

### I5 — Idle timeout for orchestration runs

**Complexity:** 7/10 | **Value:** 9/10 | **Files:** `webhook_receiver/runner.py`, `webhook_receiver/config.py`, `webhook_receiver/watchdog.py` (new), `compose.yaml`

**Current state:** `runner.py` fires `subprocess.Popen` and forgets. No timeout, no monitoring, no kill logic. A stuck agent runs indefinitely, consuming API credits.

**Reference — original project's watchdog (`run_opencode_prompt.sh:165-444`):**

- `IDLE_TIMEOUT_SECS=900` (15 min of total silence → kill)
- `READ_ONLY_GRACE_SECS=1200` (20 min with reads-only → kill)
- `HARD_CEILING_SECS=5400` (90 min absolute cap)
- Monitors client output log mtime + server `/proc/<pid>/io` (split read/write tracking)
- Subagent delegation awareness: when client is silent but server I/O is active, extends grace
- Kill strategy: SIGTERM → 10s wait → SIGKILL

**Architecture adaptation:** The current project runs `opencode run` (client) in the webhook-receiver container and `opencode serve` (server) in a separate container. `/proc/<pid>/io` monitoring of the server is **not possible** from the webhook container (different PID namespace). The timeout must rely on client stdout activity alone.

**Implementation:**

1. **Add config settings** in `config.py`:

   ```python
   @dataclass(frozen=True)
   class Settings:
       # ... existing fields ...
       runner_idle_timeout_secs: int
       runner_hard_ceiling_secs: int
       runner_subagent_grace_secs: int
   
   # In from_env():
   runner_idle_timeout_secs=int(os.environ.get("RUNNER_IDLE_TIMEOUT_SECS", "900")),
   runner_hard_ceiling_secs=int(os.environ.get("RUNNER_HARD_CEILING_SECS", "5400")),
   runner_subagent_grace_secs=int(os.environ.get("RUNNER_SUBAGENT_GRACE_SECS", "1200")),
   ```

2. **Add compose env vars** in `compose.yaml` (webhook-receiver service):

   ```yaml
   - RUNNER_IDLE_TIMEOUT_SECS=${RUNNER_IDLE_TIMEOUT_SECS:-900}
   - RUNNER_HARD_CEILING_SECS=${RUNNER_HARD_CEILING_SECS:-5400}
   - RUNNER_SUBAGENT_GRACE_SECS=${RUNNER_SUBAGENT_GRACE_SECS:-1200}
   ```

3. **Create `webhook_receiver/watchdog.py`** — a process monitor:

   ```python
   import logging
   import subprocess
   import threading
   import time
   
   logger = logging.getLogger(__name__)
   
   class Watchdog:
       """Monitor a subprocess for idle output and enforce timeouts."""
   
       def __init__(
           self,
           proc: subprocess.Popen,
           idle_timeout_secs: int = 900,
           hard_ceiling_secs: int = 5400,
           check_interval_secs: int = 30,
           label: str = "opencode",
       ):
           self.proc = proc
           self.idle_timeout = idle_timeout_secs
           self.hard_ceiling = hard_ceiling_secs
           self.check_interval = check_interval_secs
           self.label = label
           self._last_output_time = time.monotonic()
           self._start_time = time.monotonic()
           self._killed = False
           self._thread: threading.Thread | None = None
   
       def notify_output(self):
           """Call when output is received from the process."""
           self._last_output_time = time.monotonic()
   
       def start(self):
           self._thread = threading.Thread(target=self._run, daemon=True)
           self._thread.start()
   
       def _run(self):
           while self.proc.poll() is None:
               time.sleep(self.check_interval)
               now = time.monotonic()
               elapsed = now - self._start_time
               idle = now - self._last_output_time
   
               if elapsed >= self.hard_ceiling:
                   logger.error(
                       "[%s] Hard ceiling reached (%ds elapsed); terminating",
                       self.label, self.hard_ceiling,
                   )
                   self._kill()
                   break
   
               if idle >= self.idle_timeout:
                   logger.error(
                       "[%s] Idle for %ds (no output); terminating",
                       self.label, int(idle),
                   )
                   self._kill()
                   break
   
               if idle >= 60:
                   logger.info(
                       "[%s] idle=%ds elapsed=%ds pid=%s",
                       self.label, int(idle), int(elapsed), self.proc.pid,
                   )
   
       def _kill(self):
           self._killed = True
           try:
               self.proc.terminate()
               logger.info("[%s] Sent SIGTERM to pid=%s", self.label, self.proc.pid)
           except ProcessLookupError:
               return
           try:
               self.proc.wait(timeout=10)
           except subprocess.TimeoutExpired:
               logger.warning("[%s] SIGTERM timeout; sending SIGKILL to pid=%s", self.label, self.proc.pid)
               self.proc.kill()
   
       @property
       def was_killed(self) -> bool:
           return self._killed
   ```

4. **Integrate watchdog in `runner.py`:**

   ```python
   from webhook_receiver.watchdog import Watchdog
   
   def dispatch_to_opencode(settings: Settings, prompt: str) -> None:
       # ... setup ...
       proc = subprocess.Popen(
           cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
           start_new_session=True, text=True,
       )
   
       watchdog = Watchdog(
           proc,
           idle_timeout_secs=settings.runner_idle_timeout_secs,
           hard_ceiling_secs=settings.runner_hard_ceiling_secs,
           label=f"run-{prompt_path.stem}",
       )
   
       def _stream(pipe, label):
           try:
               for line in iter(pipe.readline, ""):
                   if line:
                       watchdog.notify_output()
                       if not should_filter(line):
                           logger.info("[%s] %s", label, line.rstrip())
           except ValueError:
               pass
   
       threading.Thread(target=_stream, args=(proc.stdout, "opencode"), daemon=True).start()
       threading.Thread(target=_stream, args=(proc.stderr, "opencode-err"), daemon=True).start()
       watchdog.start()
   ```

5. **Subagent delegation handling:** The `opencode run` client with `--print-logs` emits output during subagent work (tool calls, reasoning). Each output line calls `watchdog.notify_output()`, resetting the idle timer. If the client goes completely silent during a subagent delegation (unlikely with `--print-logs`), the `IDLE_TIMEOUT_SECS=900` (15 min) provides ample grace.

**Validation:**

- Set `RUNNER_IDLE_TIMEOUT_SECS=60` for testing → trigger a run → verify process is killed after 60s of silence
- Set `RUNNER_HARD_CEILING_SECS=120` → verify process is killed after 120s regardless of activity
- Normal run with active output → verify no premature kills
- Check container logs for watchdog status lines

---

## Group C — Deferred

### I4 — K8s IaC deployment resources

**Complexity:** 9/10 | **Value:** 5/10

**Current state:** Only Docker Compose deployment exists. No Kubernetes manifests.

**Recommended approach:** Use **Helm** chart or **Kustomize** overlays for provider-agnostic deployment. This is a large-scope task that should be planned separately with its own spec.

**Deferred rationale:**

- Current development and testing works with Docker Compose
- K8s deployment is needed for production but doesn't block any current functionality
- Requires decisions on: Helm vs Kustomize vs Pulumi, namespace strategy, secrets management (Sealed Secrets vs External Secrets Operator), ingress controller choice, resource limits, HPA configuration
- Should be scoped as a separate epic with its own planning document

**When ready, the implementation should cover:**

- Deployment manifests for: orchestratorservice, webhook-receiver, webhook-proxy (Caddy)
- ConfigMap for Caddyfile, opencode.json
- Secret management for API keys, webhook secret, server password
- Service definitions with internal networking
- PersistentVolumeClaims for workspace and memory volumes
- Ingress resource for webhook endpoint
- Resource requests/limits based on observed usage
- Health check probes (`/health` endpoint already exists)
- Optional: HPA for webhook-receiver based on queue depth

---

## Validation Plan

After implementing each issue, run the full validation suite:

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

### Per-issue validation

| Issue | Automated Test | Manual Validation |
|-------|---------------|-------------------|
| I6 | `test/test-compose-config.sh` passes; negative test confirms enforcement | `unset OPENCODE_SERVER_PASSWORD && docker compose config` fails |
| I2 | New pytest test: verify stdout is captured and logged | Trigger webhook → see opencode output in container logs |
| I3 | New pytest test: verify log messages at each lifecycle stage | Set `WEBHOOK_LOG_LEVEL=debug` → full lifecycle visible |
| I1 | New pytest test: verify blacklist filters target patterns, passes others | Trigger run → `message.part.delta` lines absent, other lines present |
| I7 | Pester test: verify URL resolution with various env var combinations | Run scripts with different env var configs → correct URL used |
| I5 | New pytest test: verify watchdog kills idle process, respects hard ceiling | Set short timeout → verify kill; normal run → no premature kill |

### Integration validation

1. Full stack: `docker compose up --build`
2. Trigger webhook via simulator (`WEBHOOK_ENABLE_SIMULATOR=1`)
3. Verify: event received (I3) → prompt assembled (I3) → dispatched → output streams (I2) with filtering (I1) → completes or times out (I5)
4. Verify compose enforcement (I6): `unset OPENCODE_SERVER_PASSWORD && docker compose up` fails
5. Verify client scripts (I7): run with custom `OPENCODE_SERVER_URL`
