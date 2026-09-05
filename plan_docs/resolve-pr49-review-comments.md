# Resolve PR #49 Review Comments

PR: https://github.com/nam20485/orchestrator-service/pull/49
Branch: `nam20485` → `development`

3 unresolved review threads from factory-droid. All changes are small and surgical.

---

## Task 1 — Fix `_PermissionAskMonitor.poll()` position-based ordering (P1)

**File:** `webhook_receiver/watchdog.py` lines 229–239

**Problem:** Current code uses `self._REPLIED_RE.search(text)` which clears `_last_ask_time` unconditionally when any reply exists in the chunk, then unconditionally sets it if any ask exists. When a chunk contains `ask → reply`, the ask was resolved but the code still marks it pending (ask found after reply clear). When a chunk contains `reply → ask`, there's a new pending ask but the ordering isn't tracked.

**Change:** Replace lines 229–239 with position-based comparison:

```python
        last_reply = None
        for m in self._REPLIED_RE.finditer(text):
            last_reply = m

        last_ask = None
        for m in self._ASK_RE.finditer(text):
            last_ask = m

        if last_reply is not None and (
            last_ask is None or last_reply.start() > last_ask.start()
        ):
            self._last_ask_time = None
            return
        if last_ask is not None:
            self._last_ask_time = now
            self._last_ask_detail = last_ask.group(0).strip()[:160]
```

**Tests:** Update any existing `_PermissionAskMonitor` tests to verify:
- `ask → reply` in same chunk → `_last_ask_time` is None (resolved)
- `reply → ask` in same chunk → `_last_ask_time` is set (new pending ask)
- Only reply, no ask → `_last_ask_time` is None
- Only ask, no reply → `_last_ask_time` is set

---

## Task 2 — Update `permission_deadlock` message + add `process_exit` (P2)

**File:** `webhook_receiver/run_narrative.py` lines 25–30

**Current state:** `permission_deadlock` already exists in both `_CLASSIFICATION_STATUS` (line 21) and `_KILL_REASON_MESSAGE` (line 29). Only the message wording and the missing `process_exit` entry need updating.

**Change:** In `_KILL_REASON_MESSAGE`, replace line 29:

```python
    "permission_deadlock": "Watchdog killed: stuck on an unanswered permission prompt",
    "process_exit": "Process exited",
}
```

(Replaces the old `"permission_deadlock": "Watchdog killed: unanswered permission ask (headless deadlock)"` and adds `process_exit`.)

---

## Task 3 — Sync `compose.development.yaml` with watchdog/health changes (P2)

**File:** `compose.development.yaml`

Mirror the following from `compose.yaml`:

### 3a. orchestratorservice — add opencode-logs volume

After the existing `volumes:` block (line 9–10), add:
```yaml
      - opencode-logs:/home/app/.local/share/opencode/log
```

### 3b. webhook-receiver — add opencode-logs read-only mount

After the existing volumes block (line 37–40), add:
```yaml
      - opencode-logs:/var/log/opencode-server:ro
```

### 3c. webhook-receiver — add env vars

After `GITHUB_TOKEN` (line 46), add:
```yaml
      - DIRECT_BODY_ALLOWED_SENDERS=${DIRECT_BODY_ALLOWED_SENDERS}
```

After `DASHBOARD_TOKEN` (line 52), add watchdog env vars:
```yaml
      - DISPATCH_TIMEOUT_SECS=${DISPATCH_TIMEOUT_SECS:-2700}
      - IDLE_TIMEOUT_SECS=${IDLE_TIMEOUT_SECS:-1800}
      - ERROR_GRACE_SECS=${ERROR_GRACE_SECS:-300}
      - HARD_CEILING_SECS=${HARD_CEILING_SECS:-5400}
      - WATCHDOG_POLL_SECS=${WATCHDOG_POLL_SECS:-30}
      - MAX_CONSECUTIVE_ERRORS=${MAX_CONSECUTIVE_ERRORS:-5}
      - WATCHDOG_DEBUG=${WATCHDOG_DEBUG:-false}
      - OPENCODE_SERVER_LOG_PATH=${OPENCODE_SERVER_LOG_PATH:-/var/log/opencode-server/opencode.log}
```

### 3d. webhook-receiver — change depends_on to health condition

Replace (line 53–54):
```yaml
    depends_on:
      - orchestratorservice
```
With:
```yaml
    depends_on:
      orchestratorservice:
        condition: service_healthy
```

### 3e. volumes — add opencode-logs

After `opencode-memory:` (line 78), add:
```yaml
  opencode-logs:
```

---

## Task 4 — Commit, push, reply, resolve

1. `git add` all changed files, commit with message: `address PR #49 review: position-based ask/reply ordering, narrative maps, dev compose sync`
2. `git push`
3. Reply to each thread via `gh api` with explanation
4. Resolve each thread via GraphQL `minimizeComment` / `resolveReviewThread`
5. Leave a summary comment on the PR
6. Verify 0 unresolved threads remain

### Thread IDs for resolution:
- `PRRT_kwDOSpOS4s6VP5dR` — watchdog.py poll ordering
- `PRRT_kwDOSpOS4s6VP5dW` — run_narrative.py maps
- `PRRT_kwDOSpOS4s6VP5dc` — compose.development.yaml sync
