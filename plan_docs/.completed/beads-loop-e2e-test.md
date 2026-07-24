# Plan: E2E BeadsLoop Dispatch Integration Test

## Goal
A committed, agent-drivable pytest integration test that proves the BeadsLoop
dispatch path works end-to-end in **seconds** (the current manual path takes
>1 hour). Runnable as one command by a human or an autonomous agent, with a
deterministic pass/fail exit code and a programmatic monitoring surface, so it
can validate large refactors and pin down failures during troubleshooting.

## Why
After large changes there is no fast, reliable way to confirm the beads dispatch
system still works. Existing tests mock `subprocess` entirely, so they do not
exercise the real `br`/`bvr`/`git` plumbing or the real command execution. This
test runs the real loop against real `br`/`git`, stubbing only the dispatch
*target* and external GitHub.

## Requirements (acceptance criteria)
1. One command runs the whole thing: `uv run pytest tests/test_e2e_beads_dispatch.py -q`. Exit code = pass/fail.
2. Completes in seconds (target < 30s), never the manual >1hr path.
3. Exercises as REAL code (no mocks): `BeadsLoop.run()` thread + poll loop;
   `discover_projects`; `_get_next_bead` (tries `bvr --robot-next` first, falls
   back to `br ready --json`); `_get_ready_beads` (real `br ready --json`);
   `create_bead_worktree` / `remove_bead_worktree` (real `git worktree
   add/remove`); `_build_bead_prompt` (best-effort context file writes — the
   test worktree lacks `plan_docs/` so context errors are expected and do not
   block); the real `subprocess.Popen` + dual stdout/stderr streaming +
   `proc.wait()` + `BD_DB` env injection in `_spawn_agent`; `_check_bead_status`
   (real `br show --json`); retry/halt state.
4. Stubs/mocks ONLY the dispatch target and external GitHub:
   - dispatch target → `tests/fixtures/stub-agent.ps1` via `PROMPT_SCRIPT`
     (zero production source change); the stub is a REAL subprocess that runs
     real `br close <id>`.
   - `push_branch` and `create_pr` → no-op patches.
5. First bead (highest-priority, unblocked) reaches `closed`; its worktree is
   created then removed; the event sequence
   `bead_picked_up → agent_spawned → agent_completed → bead_closed` is emitted;
   `push_branch`/`create_pr` called exactly once for that bead.
6. Light DAG-advance check: after the first bead closes, the loop correctly
   selects a DIFFERENT ready bead on the next poll (asserts selection-after-
   close, not completion of all beads).
7. On failure/watchdog-timeout, emit a diagnostics dump (events, retry_state,
   halted_beads, `br ready`, `br show <id>`, tail logs) so an agent can triage.
8. Skips cleanly (not errors) if `pwsh` is missing; logs `br version` at start.

## Design decisions (resolved)
- **Seam = PROMPT_SCRIPT stub** (user-approved). Point the test's
  `Settings.prompt_script` at a committed test-only `stub-agent.ps1`. The loop
  builds the EXACT production command (`pwsh -File <stub> -ServerUrl ...
  -PromptFile <path>`) and `Popen`s it for real. No production source change
  for the stub. The stub reads the bead id from the prompt and the DB from
  `$env:BD_DB` (already injected by the loop), then runs real `br close`.
- **Form = pytest e2e** (no separate CLI wrapper). pytest gives one-command run,
  exit code, assertions, and captured logs for both human and agent.
- **Loop fidelity = real `run()` thread** (the same code `__main__` starts),
  bounded by a watchdog timeout + success predicate. Validates polling, idle,
  and advance — i.e. "does the system actually work."
- **Scope = first bead completes** (+ light advance selection check).
- **Monitor/result = `EventStore` events + real `br show`/`br ready` queries +
  exit code + pytest logs.**

## Verified facts (governs implementation details)
- `pwsh` present on host: 7.6.3. CI image has 7.6.2.
- `br` honors the `BD_DB` env var: `br close <id>` works from a non-project cwd
  with only `BD_DB` set (no `--db` needed). `_spawn_agent` sets
  `env["BD_DB"] = <project_root>/.beads/beads.db` and runs `Popen` with NO cwd,
  so the stub MUST rely on `BD_DB` (not auto-discovery).
- The loop's own `br`/`bvr` commands (`_get_ready_beads`, `_check_bead_status`,
  `_get_next_bead_bvr`) use `_run_beads_cmd` which sets `cwd=project_root` for
  DB auto-discovery (not `BD_DB`). Only the stub subprocess receives `BD_DB`.
  The test must ensure `project_root` is a valid beads project so `cwd`-based
  discovery works for the loop's internal `br` queries.
- Real `br ready --json` and `br show --json` return **top-level arrays**
  (`[{...,"status":...}]`). Local `br` is 0.2.15 @ `d4b7c6d`; container pins
  0.2.15 @ `d9f8d708` — shape MAY differ in-container; the test uses whatever
  real `br` is installed and asserts on that.
- Host `git config commit.gpgsign = true` → the fixture's seed commit MUST use
  `-c commit.gpgsign=false` (does not affect production).
- `git worktree add -b <branch> <path> <base>` requires the project to have at
  least one commit → fixture commits once before `br init`.
- pytest config: `testpaths=["tests"]`; dev group has pytest, pytest-cov, httpx,
  ruff. Mirror the existing `_test_settings()` helper in
  `tests/test_integration_beads.py`.

## Anticipated finding (likely first failure)
`_check_bead_status` (`webhook_receiver/beads_loop.py:503-525`) parses only a
**dict** (`data.get("issue", data)`); real `br show --json` returns an
**array**, so it returns `"unknown"`. After a successful `br close` the loop
would see `status != "closed"` → treat the bead as failed → retry → halt.
The test fails fast (~10s) and the diagnostics dump points at it. The
conditional fix below makes the test green.

## Files

### Add: `tests/fixtures/stub-agent.ps1` (test-only)
A thin PowerShell stub invoked exactly like the production prompt script.
- Params: `-ServerUrl`, `-Workspace`, `-Model`, `-Agent`, `-PromptFile`
  (accept/ignore extras so it matches `_prompt_script_invocation`).
- Read `$PromptFile`; regex-extract the bead id from the line
  `You have been assigned Bead <id>:` (id chars: `[A-Za-z0-9._-]+`).
- Fail loudly (exit non-zero) if no id or no `$env:BD_DB`.
- Run `br close <id>` (br on PATH via inherited env; `BD_DB` points at the
  shared project DB). Print a marker line `STUB-AGENT closed <id>` to stdout for
  log visibility. Exit 0 on success, non-zero otherwise.

### Add: `tests/test_e2e_beads_dispatch.py`
Mirror `_test_settings()` from `tests/test_integration_beads.py`. Structure:
1. `_make_project(tmp_base, slug)`:
   - `mkdir tmp_base/<slug>`; `git init --initial-branch=main`.
   - Write `README.md`; `git -c commit.gpgsign=false -c user.email=.. -c user.name=.. commit -m init`.
   - `br init`; verify `.beads/beads.db` exists (if not, `pytest.fail("br init
     did not create beads.db")`). Create T1(p1, unblocked), T2(p2, unblocked),
     T3(p2); capture ids via Python JSON parsing (not `jq`):
     `json.loads(subprocess.check_output(["br", "create", ...]))["id"]` — handle
     both dict and array shapes from `br create --json`.
     `br dep add T3 T1` (T3 blocked by T1).
   - Return project_root + id map.
2. `_settings(tmp_base, stub_script, project_root)`: settings with:
   - `beads_workspace_root=tmp_base` — the **parent** dir; used by
     `discover_projects` to scan for `<tmp_base>/<slug>/.beads/` subdirs.
   - `workspace=project_root` — the **specific** project; used by
     `_spawn_agent` as the workspace override for the prompt script invocation.
   - `beads_poll_interval=1`, `beads_max_retries=3`,
     `prompt_script=stub_script`.
3. Test body:
   - Build project + `EventStore()` + `BeadsLoop(settings, event_store)`.
   - `monkeypatch.setattr` `webhook_receiver.beads_loop.push_branch` and
     `create_pr` to no-op callables (record call args).
   - Start `loop.run()` in a daemon thread.
   - Watchdog loop (60s, configurable via `E2E_WATCHDOG_TIMEOUT` env var):
     poll `event_store.recent(500)` until a `bead_closed` event for T1 appears
     (check `event["type"] == "bead_closed"` and
     `event["data"]["bead_id"] == t1_id`). Helper:
     ```python
     def _has_event(store, event_type, bead_id):
         return any(
             e["type"] == event_type and e["data"].get("bead_id") == bead_id
             for e in store.recent(500)
         )
     ```
   - `loop.stop()`; join the thread (timeout).
   - Assertions (AC #5/#6):
     - T1 closed: `br show T1 --json` status == "closed".
     - Event sequence: `bead_picked_up → agent_spawned → agent_completed →
       bead_closed` all present for T1 (use `_has_event` helper).
     - `push_branch`/`create_pr` each called exactly once (assert on mock call
       count). Use `monkeypatch.setattr("webhook_receiver.beads_loop.push_branch",
       mock_push)` — target the **lookup site** (`beads_loop` module), not the
       **definition site** (`workspace` module).
     - Worktree removed: `assert not os.path.exists(
       os.path.join(project_root, ".worktrees", t1_safe_id))`.
     - DAG advance: on the next poll a `bead_picked_up` for a bead != T1
       (advance) OR a clean idle log. If `bvr` is not available, the advance
       check expects `br ready` fallback selection.
   - On watchdog timeout: `pytest.fail` with diagnostics dump (events,
     `loop.retry_state`, `loop.halted_beads`, `br ready --json`, `br show T1`,
     captured log tail). If T1 is closed in `br` but present in
     `halted_beads`, the message explicitly suggests the
     `_check_bead_status` array-shape mismatch.
   - Guard: `pytest.mark.skipif(shutil.which("pwsh") is None)`.
   - Log `br version` AND `bvr --version` at test start for version/shape
     traceability. If `bvr` is not on PATH, log "bvr not available — loop will
     use br ready fallback" (this is normal, not an error).
   - Teardown: clean up `/tmp/orchestrator-webhook/bead-*.md` temp files
     matching the test's bead IDs (the loop writes prompt files there but
     never cleans them up).

### Conditional edit: `webhook_receiver/beads_loop.py` (`_check_bead_status`)
ONLY if the test fails on status detection (i.e. real `br show` returns an
array). Handle the array shape before the dict branch, e.g.:
```python
data = json.loads(stdout)
if isinstance(data, list):
    data = data[0] if data else {}
if isinstance(data, dict):
    issue = data.get("issue", data)
    return str(issue.get("status", "unknown")).lower()
return "unknown"
```
This is a correctness fix (not a test-only hack). Re-run the test until green.

## Implementation steps (ordered)
1. Add `tests/fixtures/stub-agent.ps1`.
2. Add `tests/test_e2e_beads_dispatch.py`.
3. Run `uv run pytest tests/test_e2e_beads_dispatch.py -q`.
4. If it fails on the array-shape mismatch: apply the conditional fix to
   `_check_bead_status`, re-run until green. (If it fails for another reason,
   triage from the diagnostics dump — that IS the validation result.)
5. Run full suite: `uv run pytest tests/ -q` (no regressions).
6. Run `pwsh -NoProfile -File ./scripts/validate.ps1 -Test`.

## Validation plan
- Green: `uv run pytest tests/test_e2e_beads_dispatch.py -q`.
- No regressions: `uv run pytest tests/ -q`.
- CI parity: `./scripts/validate.ps1 -Test`.
- Manual eyeball: run the single test; logs show `STUB-AGENT closed <id>` and
  the `bead_closed` event; loop then advances to the next ready bead.

## Risks / notes
- **Version-shape variance:** container `br` (`d9f8d708`) may return a different
  `br show` shape than local (`d4b7c6d`). The test asserts on the real installed
  `br` and logs its version, so the result is interpretable either way. The
  conditional `_check_bead_status` fix should handle BOTH array and dict shapes
  defensively.
- **pwsh dependency:** required because the production `_spawn_agent` always
  uses `pwsh -File`. Test skips (not errors) if absent.
- The stub closes the bead without making a git commit in the worktree; that is
  intended (the success path runs mocked push/PR anyway, and we are validating
  the loop, not the agent's coding).

## Out of scope
- Completing more than the first bead.
- Real GitHub push/PR (mocked) and real opencode/LLM agent (stubbed).
- A separate CLI wrapper script (pytest is the single entry point).
- Testing against the exact in-container `br` binary in this task (covered by CI
  build/later; this plan uses the host-installed `br`).
