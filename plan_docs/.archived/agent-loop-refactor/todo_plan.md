# Plan: T1 (ignore files) + T2 (test coverage) + T3 (integration tests)

## Context

Three cleanup tasks from `plan_docs/todo.md`:

- **T1**: `.dockerignore` and `.gitignore` need updating — `.gitignore` is bloated with irrelevant boilerplate, both files missing project-specific entries
- **T2**: Test coverage gaps — `runner.py` at 30%, `simulator_templates.py` at 69%, `filters.py` at 85%
- **T3**: Integration tests for the three pipeline stages (webhook→prompt, prompt→dispatch, beads→execution)

Current state: 89 tests pass, 89% overall coverage. `beads_loop.py`, `config.py`, `workspace.py`, `github.py` are at 100%.

---

## T1: Update .dockerignore and .gitignore

### .gitignore changes

**Add** (project-specific, currently missing):

- `.qwen/` — Qwen AI machine-local state
- `plan_docs/todo.md` — personal planning artifact

**Trim** (remove irrelevant boilerplate sections that add noise):

- Django stuff (lines 58-62): `*.log`, `local_settings.py`, `db.sqlite3*` — keep `*.log` only, move it to a generic section
- Flask stuff (lines 64-66): `instance/`, `.webassets-cache` — remove
- Scrapy stuff (lines 68-69): `.scrapy` — remove
- Sphinx documentation (lines 71-72): `docs/_build/` — remove
- PyBuilder (lines 74-76): `.pybuilder/`, `target/` — remove
- Jupyter Notebook (lines 78-79): `.ipynb_checkpoints` — remove
- IPython (lines 81-83): `profile_default/`, `ipython_config.py` — remove
- pyenv (lines 85-88): commented out, remove
- pipenv (lines 90-95): commented out, remove
- UV (lines 97-101): commented out, remove
- poetry (lines 103-109): commented out, remove
- pdm (lines 111-118): `.pdm-python`, `.pdm-build/` — remove
- pixi (lines 120-125): `.pixi` — remove
- PEP 582 (lines 127-128): `__pypackages__/` — remove
- Celery stuff (lines 130-132): remove
- Redis (lines 134-137): `*.rdb`, `*.aof`, `*.pid` — keep `*.pid` only (generic)
- RabbitMQ (lines 139-142): remove
- ActiveMQ (lines 144-145): remove
- SageMath (lines 147-148): remove
- Spyder (lines 160-161): remove
- Rope (lines 164-165): remove
- mkdocs (lines 167-168): remove
- Pyre (lines 175-176): remove
- pytype (lines 178-179): remove
- Cython debug (lines 181-182): remove
- PyCharm (lines 184-189): commented out, remove
- Abstra (lines 191-195): remove
- VS Code (lines 197-204): keep `tempCodeRunnerFile.py`, remove the rest
- Marimo (lines 212-215): remove
- Streamlit (lines 217-218): keep `auth/auth.json`, remove `.streamlit/secrets.toml`

**Result**: ~80 lines (down from 227), focused on this project's actual stack.

### .dockerignore changes

**Add**:

- `.qwen/` — Qwen AI state
- `.kilo/` — Kilo CLI state (already in .gitignore, missing from .dockerignore)
- `plan_docs/todo.md` — personal planning artifact

No removals needed — current .dockerignore is well-structured.

---

## T2: Add test coverage

### runner.py (30% → target ~80%)

Missing lines and what to test:

| Lines | Function | Test |
|-------|----------|------|
| 17 | `_base_args` | Returns correct pwsh args from settings |
| 30-33 | `_prompt_script_invocation` | Validates .ps1 extension, builds full command; raises ValueError for non-.ps1 |
| 52-61 | `_stream_to_logger_and_file` | Reads pipe lines, writes to file, logs non-filtered lines, suppresses filtered lines |
| 66-113 | `dispatch_to_opencode` | Creates temp prompt file, spawns subprocess, starts streaming threads |

Tests to add in `tests/test_runner.py` (new file):

1. `test_base_args_builds_correct_pwsh_args` — verify settings → arg list mapping
2. `test_prompt_script_invocation_valid_ps1` — verify full command with valid .ps1 script
3. `test_prompt_script_invocation_rejects_non_ps1` — verify ValueError for .sh script
4. `test_stream_to_logger_writes_to_file_and_logs` — mock pipe, verify file write + logger call
5. `test_stream_to_logger_suppresses_filtered_lines` — mock pipe with blacklisted content, verify file write but no logger call
6. `test_dispatch_creates_temp_prompt_file` — verify temp file creation with correct content
7. `test_dispatch_spawns_subprocess_with_correct_cmd` — mock subprocess.Popen, verify command args
8. `test_dispatch_starts_streaming_threads` — verify two daemon threads started for stdout/stderr

### simulator_templates.py (69% → target ~90%)

Missing lines and what to test:

| Lines | Function | Test |
|-------|----------|------|
| 54 | `get_template` | Unknown event raises ValueError |
| 70 | `get_template` | "custom" event returns base payload |
| 83-93 | `get_template` | "pull_request" event returns correct payload |
| 95-106 | `get_template` | "issue_comment" event returns correct payload |
| 108-117 | `get_template` | "workflow_run" event returns correct payload |
| 139 | `merge_template` | PR number override in pull_request payload |

Tests to add in `tests/test_simulator_templates.py` (new file):

1. `test_get_template_unknown_event_raises` — verify ValueError
2. `test_get_template_custom_event` — verify base payload with action
3. `test_get_template_pull_request` — verify PR payload structure
4. `test_get_template_issue_comment` — verify comment payload structure
5. `test_get_template_workflow_run` — verify workflow_run payload structure
6. `test_merge_template_pull_request_number` — verify PR number override

### filters.py (85% → target 100%)

Missing lines and what to test:

| Lines | Function | Test |
|-------|----------|------|
| 15 | `_load_patterns` | Custom TRACE_BLACKLIST_PATTERNS env var parsed correctly |
| 26 | `should_filter` | Returns True for blacklisted line |

Tests to add in `tests/test_filters.py` (new file):

1. `test_should_filter_matches_default_blacklist` — verify default patterns filter bus delta messages
2. `test_should_filter_passes_normal_lines` — verify non-matching lines pass through
3. `test_load_patterns_from_env` — verify custom patterns override defaults

### Coverage exclusions

Add to `pyproject.toml` `[tool.coverage.report] exclude_lines`:

- `"if __name__"` — module entry points not worth testing
- `"except ImportError"` — optional dependency guards

Current exclusions are already good: `pragma: no cover`, `if TYPE_CHECKING:`, `raise NotImplementedError`.

### HTML & PR comment report generators

- **HTML generator**: `simulator.py` serves `simulator.html` with secret injection — already 92% covered. The missing lines (32, 63-64) are error paths (file not found, ValueError → HTTPException). Add 2 tests to `test_simulator.py`.
- **PR comment report**: No dedicated generator exists. PR creation is in `workspace.py:create_pr` (calls `gh pr create`) — 100% covered via mocks in `test_beads_loop.py`. No additional work needed.

---

## T3: 3-Stage Integration Tests

### Stage boundaries

```
Stage 1: Webhook → Prompt Assembly
  Input:  HTTP POST /webhooks/github (signed payload)
  Output: Orchestrator prompt string (markdown)
  Code:   app.py → prompts.py

Stage 2: Prompt → Dispatch
  Input:  Prompt string + Settings
  Output: Subprocess spawned, streaming threads started
  Code:   runner.py (dispatch_to_opencode)

Stage 3: Beads → Execution
  Input:  Beads DAG (.beads/ directory)
  Output: Agent spawned, bead closed, PR created
  Code:   beads_loop.py → workspace.py
```

### Test strategy: bottom-up (stage 3 → 2 → 1)

#### Stage 3: BeadsLoop integration (`tests/test_integration_beads.py`)

Test the full beads execution path with mocked subprocess but real workspace logic:

1. `test_beads_loop_poll_to_close_happy_path` — mock `br ready` returns one bead, mock `bvr --robot-next` returns same bead, mock agent subprocess succeeds, mock `br show` returns closed. Verify: workspace created, agent spawned with correct prompt, PR created, workspace cleaned.
2. `test_beads_loop_retry_on_agent_failure` — mock agent subprocess fails (non-zero exit), mock `br show` returns open. Verify: retry state incremented, second attempt includes error context in prompt, max retries halts with error log.
3. `test_beads_loop_workspace_creation_failure` — mock `git clone` fails. Verify: retry state incremented, no agent spawned.
4. `test_beads_loop_push_failure_still_clears_retry` — mock agent succeeds, mock `git push` fails. Verify: retry state cleared (bead was closed), error logged but not fatal.
5. `test_beads_loop_concurrent_beads_lock` — two beads ready, first one starts processing. Verify: second bead skipped (already active), lock released after completion.

#### Stage 2: Dispatch integration (`tests/test_integration_dispatch.py`)

Test the full dispatch path with a real subprocess that exits immediately:

1. `test_dispatch_end_to_end_with_echo` — use `echo "done"` as a fake prompt script (via a temp .sh file, but runner.py requires .ps1 — so use `pwsh -c "Write-Output done"` or mock the .ps1 validation). Verify: temp prompt file created, subprocess spawned, stdout/stderr files written, streaming threads complete.
2. `test_dispatch_prompt_file_contains_full_prompt` — verify the temp file written by dispatch contains the exact prompt string.
3. `test_dispatch_concurrent_dispatches_use_unique_files` — dispatch twice rapidly, verify two distinct prompt files created.

**Note**: These tests require `pwsh` to be available. If not available in CI, mark with `@pytest.mark.skipif` and run only when `pwsh` is on PATH.

#### Stage 1: Webhook → Prompt integration (`tests/test_integration_webhook.py`)

Test the full HTTP → prompt path with mocked dispatch:

1. `test_webhook_to_prompt_assembly_issues_event` — POST a real issues payload, verify dispatch called with a prompt containing the event JSON, delivery ID, and orchestration template.
2. `test_webhook_to_prompt_truncates_large_payload` — POST a payload exceeding max_payload_chars, verify prompt contains truncation notice.
3. `test_webhook_to_prompt_ping_skips_dispatch` — POST ping event, verify 200 response, dispatch NOT called.
4. `test_webhook_to_prompt_bad_signature_rejects` — POST with wrong signature, verify 401, dispatch NOT called.
5. `test_webhook_to_prompt_allowed_events_filter` — configure allowed_events={"pull_request"}, POST issues event, verify 202 "ignored", dispatch NOT called.

### Failure/retry logic tests

These are critical and complex. Add to the appropriate stage test files:

**Stage 3 retry tests** (in `test_integration_beads.py`):

- `test_beads_loop_injects_previous_logs_on_retry` — first attempt fails, verify second prompt contains "WARNING: Previous attempt failed" + stderr excerpt
- `test_beads_loop_halt_after_max_retries` — exhaust retries, verify error logged, bead left open, no further spawn attempts
- `test_beads_loop_clears_retry_state_on_success` — succeed after retry, verify retry state removed

**Stage 2 failure tests** (in `test_integration_dispatch.py`):

- `test_dispatch_handles_subprocess_crash` — mock Popen to raise OSError, verify error logged, no crash
- `test_dispatch_handles_prompt_file_write_failure` — mock tempfile.mkstemp to fail, verify error logged

---

## Implementation order

1. **T1** — ignore files (quick, no dependencies)
2. **T2** — unit tests for runner.py, simulator_templates.py, filters.py (builds foundation for T3)
3. **T3** — integration tests (depends on T2 coverage for stage 2)

## Validation

After each task:

```bash
pwsh -NoProfile -File ./scripts/validate.ps1 -All
```

Expected outcomes:

- T1: No validation impact (ignore files don't affect tests)
- T2: Coverage increases from 89% → ~95%+; all new tests pass
- T3: All integration tests pass; coverage may increase further
