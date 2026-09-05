# Plan: Backup model fallback — main + subagent tiers

**Status:** PLANNED — not implemented
**Date:** 2026-09-05
**Trigger incident:** `qwen-daemon-client-golf83` dispatch killed after the primary model's provider quota exhausted (`AI_APICallError: Too Many Requests` on `qwencloud/qwen3.8-max`).

---

## Incident evidence (why this plan exists)

Run `ses_f8e24df53ffeykDFiWSZyFkbt3`, dispatched 2026-09-05 13:56:19 UTC:

```
13:56:19 runner  Dispatching orchestration run … prompt_bytes=42869
13:56:19 runner  [watchdog] starting pid=212 idle_timeout=1800s hard_ceiling=5400s poll=30s max_errors=5
13:56:22 server  message=stream providerID=zai-coding-plan modelID=glm-4.5-air small=true agent=title      ← OK
13:56:26 server  message=stream providerID=qwencloud modelID=qwen3.8-max small=false agent=orchestrator     ← primary
13:56:27 server  level=ERROR message="stream error" … error.error="AI_APICallError: Too Many Requests"     ← quota
13:57:49 runner  [watchdog] elapsed=90s line_idle=87s server_log_idle=0s errors=0/5 lines=3                  ← blind
13:58:19 runner  [watchdog] elapsed=120s … errors=0/5 lines=3
13:59:19 runner  [watchdog] elapsed=180s line_idle=177s server_log_idle=90s errors=0/5 lines=3
~13:59    user kill (heartbeats stop)
```

The QwenCloud weekly request cap was exhausted, so every primary-tier call 429s. The
zai tier (title generation, and by design all subagents) still worked.

---

## Current model topology (verified 2026-09-05)

| Tier | Observed in run | Source of truth | Notes |
|------|-----------------|-----------------|-------|
| **Main / orchestrator** | `qwencloud/qwen3.8-max` @ `high` | `webhook_receiver/config.py:139-140` → `runner.py:236-241` (`-Model`/`-Variant`) → `scripts/prompt.ps1:85,94` → `opencode run --model/--variant` | CLI flag is the **highest** precedence — it beat the cloned project's own `model` setting (`zai-coding-plan/glm-5.2`, project `opencode.jsonc`) |
| **small_model (titles)** | `zai-coding-plan/glm-4.5-air` | Cloned project `/workspace/<repo>/.opencode/opencode.jsonc` → overrides image global `~/.config/opencode/opencode.json` (`glm-5.3-flash`) | **Project-over-global precedence proven live** in this run |
| **Subagents (9 roles)** | not reached (run died first) | Two layers: image global `image/.opencode/opencode.json` `agent.*` blocks (`zai-coding-plan/glm-5.3-flash` @ `xhigh`) AND cloned repo `.opencode/agents/*.md` frontmatter (template-seeded zai) | zai tier — unaffected by a qwencloud outage |
| **Deployed image global** | `small_model: zai-coding-plan/glm-5.3-flash` confirmed inside container | `docker compose exec orchestratorservice cat /home/app/.config/opencode/opencode.json` | matches branch head; no image lag observed |

**Structural fact:** main tier = qwencloud; small + subagent tiers = zai. The stack is
already provider-diverse by design — today only the main tier died.

## Gap analysis

1. **No native fallback in opencode v1.18.4** (verified): `opencode run --help` has
   `-m/--model` but no fallback list and no config-override flag (`-c` is
   `--continue`); the config schema exposes a single `model` string. Failover must
   therefore live at the dispatch layer.
2. **Quota errors are invisible to the watchdog's error counter.**
   `watchdog.py` `_ERROR_PATTERNS` includes `AI_APICallError`, but it only scans
   **client** stdout lines; the 429 appeared solely in the **server** slog. The
   client emitted 3 lines, none matching, so `errors` stayed 0/5.
3. **Slow, misleading termination.** With quota dead the client goes silent
   (`line_idle` climbs) while the server keeps retrying and writing its log
   (`server_log_idle` resets, suppressing `effective_idle`). Worst case the run
   lingers until `idle_timeout=1800s` or even `hard_ceiling=5400s`, then classifies
   as `idle_timeout`/`hard_ceiling` — masking the real cause.
4. **No failover path.** The run simply dies; recovery is a manual re-trigger after
   the weekly quota resets, or a manual env flip.

## Goals / non-goals

**Goals**
- Detect provider-quota exhaustion within ~2 watchdog polls (<2 min).
- Automatically fail the **main** model over once to a configured backup, then re-dispatch the same prompt.
- Provide an opt-in backup path for the **subagent** tier (for a zai-tier outage).
- Classify and report quota deaths distinctly (`quota_exhausted`) so dashboards and run reports show the true cause.

**Non-goals**
- Multi-provider routing / cost optimization; parallel model use.
- Changing the default primary model.
- Per-request retry logic (opencode already retries inside the stream).

## Options

**(A) Phase 1 — Watchdog quota fast-fail** (`webhook_receiver/watchdog.py`, `runner.py`, `run_narrative.py`, tests)
- Add quota patterns to `_ERROR_PATTERNS`: `Too Many Requests`, `\b429\b`, `quota` (client-side catch, matching the existing `Rate limit` / `Usage limit reached` entries).
- Extend `_ServerLogMonitor` (or add a sibling scanner) to pattern-scan the **server log** `/var/log/opencode-server/opencode.log` for the quota signature — this is the only signal that fired today.
- New kill reason `quota_exhausted`: fire after ~2 consecutive positive polls (60s grace), not 1800s.
- Register `quota_exhausted` in `run_narrative._CLASSIFICATION_STATUS` (`"error"`) and `_KILL_REASON_MESSAGE` ("Watchdog killed: provider quota exhausted").

**(B) Phase 2 — Runner auto re-dispatch with backup main model** (`config.py`, `runner.py`, `compose.yaml`, tests)
- New env: `OPENCODE_BACKUP_MODEL` (suggested `zai-coding-plan/glm-5`), `OPENCODE_BACKUP_VARIANT` (suggested `high`).
- On `quota_exhausted` classification **and** retry budget unused (persist a `fallback_used` marker in the run manifest + issue comment to prevent ping-pong loops), re-dispatch the identical prompt with `-Model $backup -Variant $backup_variant`.
- Post an issue comment: primary quota exhausted → retrying on `<backup>`.

**(C) Phase 3 — Subagent-tier backup via project config injection** (`scripts/prompt.ps1` or a runner pre-dispatch step)
- New env: `OPENCODE_SUBAGENT_BACKUP_MODEL` (unset by default).
- When set, write/merge `agent: { <name>: { model: … } }` for the 9 roles into the workspace project `.opencode/opencode.jsonc` before dispatch.
- Mechanism is **proven viable**: project-over-global precedence was observed live in this incident (project `small_model` glm-4.5-air beat image global glm-5.3-flash).
- Caveat to verify first: whether project `agents/*.md` frontmatter overrides the config `agent.*` block (or vice versa) — determines whether the merge must touch the agent files instead of the config.

**(D) Upstream router (OpenRouter auto-routing / LiteLLM fallback container)** — rejected: extra hop and cost; the qwencloud plan quota does not carry through routers (fallback would burn pay-as-you-go credits); adds a service to operate.

**(E) Manual runbook (zero-code stopgap — adopt immediately, documentation only)**: on quota exhaustion, flip the main tier via env and re-trigger:
```
export OPENCODE_MODEL=zai-coding-plan/glm-5   # in the shell/`.env` that feeds compose
./scripts/dc.ps1 up nam20485 --force-recreate
# then re-add the direct-body label on the dispatch issue to fire issues.labeled
```

## RECOMMENDATION

**Phase (A) then (B)** — together they close today's failure class end-to-end: detect
in <2 min instead of 30–90 min, label it truthfully, and self-heal once per dispatch
on a backup provider tier that is structurally independent (qwencloud main → zai
backup). **(C) deferred** until a zai-tier exhaustion is actually observed (today it
was the healthy tier). **(E) documented now** as the break-glass procedure.
Robustness caveats: the retry budget must be persisted (manifest marker + issue
comment), not in-memory, so a receiver restart cannot double-dispatch; and the
quota scanner needs its own grace window so a single transient 429 burst (already
auto-retried) does not trigger failover.

## Env / config additions (when implemented)

| Variable | Default | Consumer | Phase |
|----------|---------|----------|-------|
| `OPENCODE_BACKUP_MODEL` | `zai-coding-plan/glm-5` | runner re-dispatch | B |
| `OPENCODE_BACKUP_VARIANT` | `high` | runner re-dispatch | B |
| `OPENCODE_SUBAGENT_BACKUP_MODEL` | unset | project-config injection | C |
| (kill reason) `quota_exhausted` | — | watchdog / narrative maps | A |

## Test plan

- Unit: watchdog quota patterns match `AI_APICallError: Too Many Requests` and miss `Rate limit reset in 5s` recovery lines; server-log scanner fires only after grace; narrative maps carry `quota_exhausted`; runner retry-once guard blocks a second fallback for the same delivery (manifest marker).
- Integration: `WEBHOOK_ENABLE_SIMULATOR=1` dispatch with a deliberately invalid provider key to force the quota signature end-to-end; assert kill reason, issue comment, and (Phase B) the second dispatch carries `-Model <backup>`.
- Manual e2e after the next real quota reset: revert to primary, confirm no fallback engages on healthy runs.

## Open questions

- QwenCloud weekly quota reset window (scheduling/budgeting; not blocking).
- Precedence between project `agents/*.md` frontmatter and config `agent.*` blocks in opencode v1.18.4 — must be verified before Phase 3 (determines the injection target).
- Whether `_ServerLogMonitor` should parse content in the same class (small change) or a new `_QuotaMonitor` sibling keeps growth-detection and content-scan separate (cleaner; recommended).

## Files touched (phase mapping)

| Phase | Files |
|-------|-------|
| A | `webhook_receiver/watchdog.py`, `webhook_receiver/runner.py`, `webhook_receiver/run_narrative.py`, `tests/test_watchdog.py`, `tests/test_runner.py`, `tests/test_run_narrative.py` |
| B | `webhook_receiver/config.py`, `webhook_receiver/runner.py`, `compose.yaml`, `compose.development.yaml`, `docs/environment-variables.md`, `tests/test_config.py`, `tests/test_runner.py` |
| C | `scripts/prompt.ps1` (or runner pre-dispatch), `docs/environment-variables.md`, tests |
| E | `README.md` / `docs/environment-variables.md` (runbook section only) |
