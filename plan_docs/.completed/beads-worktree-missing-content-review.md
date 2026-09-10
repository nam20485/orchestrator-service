# Review of `beads-worktree-missing-content.md`

## Overall Assessment

The plan is well-structured and correctly identifies the root cause: two skills fail to commit `plan_docs/application_plan.md` to the project repo's default branch, causing per-bead worktrees to be empty. The three-layer fix (A/B/C) is sound in design. However, the proposed automated tests have a **critical mocking bug** that would cause them to fail or pass vacuously, and several line-number references into `beads_loop.py` are off.

---

### Critical Issues (Must Fix Before Implementation)

#### 1. `test_poll_skips_project_with_untracked_plan` will not test what it claims — `mock_empty_beads` defeats the guard

**Plan §Verification Plan** says:
```python
def test_poll_skips_project_with_untracked_plan(tmp_path, mock_empty_beads):
    ...
    loop._poll_and_process_project("proj", str(project))
    assert not (project / ".worktrees").exists()
    assert loop._active_beads == set()
```

**Actual `mock_empty_beads` fixture** (`tests/test_beads_loop.py:48-57`):
```python
@pytest.fixture
def mock_empty_beads() -> Any:
    with patch("webhook_receiver.beads_loop.subprocess.run") as mock_run:
        mock_run.return_value = _mock_result("")
        yield mock_run
```

And `_mock_result` returns `returncode=0` by default:
```python
def _mock_result(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
```

The proposed `_plan_tracked()` calls `subprocess.run(["git", "ls-files", "--error-unmatch", ...])` — which goes through `webhook_receiver.beads_loop.subprocess.run`, **the exact same symbol patched by `mock_empty_beads`**. The mock always returns `returncode=0`, so `_plan_tracked()` **always returns `True`** — even when the plan is untracked. The guard never fires. The test asserts the guard worked, but it didn't — `_get_next_bead` returns `None` (also mocked to empty stdout) and the method returns early via the `if bead is None: return` path, not the guard.

**Fix:** Either:
- Drop `mock_empty_beads` from this test and mock `subprocess.run` with a `side_effect` that returns `returncode=1` (untracked) for `git ls-files` calls but delegates `git init` to the real subprocess, OR
- Patch `_plan_tracked` directly: `with patch("webhook_receiver.beads_loop._plan_tracked", return_value=False):`, which cleanly isolates the guard test from the git internals.

#### 2. `test_poll_processes_project_with_committed_plan` has the same mock collision + missing fixtures

**Plan §Verification Plan** says:
```python
def test_poll_processes_project_with_committed_plan(tmp_path, mock_beads_with_ready):
```

`mock_beads_with_ready` does not exist anywhere in the test file. Additionally:
- `import subprocess` is not in `tests/test_beads_loop.py` (imports: `json`, `os`, `Path`, `Any`, `MagicMock`, `patch`, `pytest`), so `subprocess.run(...)` calls would raise `NameError`.
- `os.environ` is used but `os` IS imported (line 4), so that's fine.
- The real `git commit` call requires `GIT_COMMITTER_NAME`/`GIT_COMMITTER_EMAIL` — the test sets only `GIT_AUTHOR_*`, not `GIT_COMMITTER_*`. Git defaults to the author values in most configs, but on CI without a global git config this can fail.

**Fix:**
- Add `import subprocess` to the test file.
- Define `mock_beads_with_ready` or replace with explicit `@patch` decorators.
- Add `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL` to the commit env dict.

---

### Significant Gaps

#### 3. Log-spam mitigation described but not implemented

**Plan §B notes** (lines 293-294):
> To avoid log spam on every 10 s poll, gate the ERROR behind a per-project "already warned" set (e.g. `self._plan_warned: set[str]`), resetting when the plan later appears.

This is a critical operational concern — an untracked plan would generate an ERROR log line **every 10 seconds** indefinitely. But the implementation sketch code does not include the `_plan_warned` set — it only shows a bare `logger.error(...)` + `return`. An implementor following the sketch literally will ship the spam.

**Fix:** Include the `_plan_warned` logic directly in the implementation sketch code block:
```python
def _poll_and_process_project(self, project_slug: str, project_root: str) -> None:
    if not _plan_tracked(project_root):
        if project_slug not in self._plan_warned:
            logger.error(...)
            self._plan_warned.add(project_slug)
        return
    # ... rest
    self._plan_warned.discard(project_slug)  # reset on recovery
```

#### 4. `image/AGENTS.md` listed in scope table but has no implementation guidance

**Plan §Scope of Changes** includes:
| — | `image/AGENTS.md` (container) / root `AGENTS.md` | Document the "plan must be committed to main before beads run" contract as a rule |

This is listed alongside code changes but has no section in "Recommended Fix" (A/B/C). It's unclear whether this is a required change or a nice-to-have, what the text should say, or where in `AGENTS.md` it belongs. An implementor may skip it or write an inappropriate rule.

**Fix:** Either add a section D to "Recommended Fix" with the proposed text and insertion point, or remove it from the scope table and note it as a follow-up.

---

### Minor Issues / Improvements

#### 5. Line number references for `beads_loop.py` are off

**Plan §Evidence** (line 48) says:
> `webhook_receiver/workspace.py:263` (`create_bead_context.read_project_overview()`)

This is correct (`workspace.py:263`). But:

- **§B (line 240)**: "at the top of `_poll_and_process_project` (line 115)" — actual: **line 146**.
- **§B (line 240)**: "right before `_get_next_bead` (line 119)" — actual: **line 150**.
- **§Related (line 397-398)**: "`webhook_receiver/beads_loop.py:115-123` — `_poll_and_process_project`" — actual: **146-162+**.
- **§Related (line 399-400)**: "`webhook_receiver/beads_loop.py:378-399` — the bead prompt" — actual: `_build_bead_prompt` starts at **line 374**; the prompt assembly begins at **line 409**.

These suggest the line numbers were captured before a code change added ~30 lines earlier in the file. The plug-in point description is correct ("top of `_poll_and_process_project`, before `_get_next_bead`"), so this won't block implementation, but stale line numbers reduce trust in the document.

**Fix:** Update all `beads_loop.py` line references to current values.

#### 6. Redundant `import subprocess` in the implementation sketch

**Plan §B (line 247)**:
```python
# webhook_receiver/beads_loop.py
import subprocess
```

`subprocess` is **already imported** at `beads_loop.py:6`:
```python
import subprocess
```

**Fix:** Remove the redundant import from the sketch.

#### 7. `_poll_and_process_project` signature mismatch in sketch

**Plan §B (line 272)** shows:
```python
def _poll_and_process_project(self, project_root: str) -> None:
```

**Actual** (`beads_loop.py:146-148`):
```python
def _poll_and_process_project(
    self, project_slug: str, project_root: str
) -> None:
```

The sketch drops `project_slug`, which is needed for the log message and the `_plan_warned` key.

**Fix:** Add `project_slug: str` to the sketch's method signature.

#### 8. Recovery one-liner (§C) references `create-beads-dag.sh`

**Plan §C (line 306)**:
```bash
git add plan_docs/ create-beads-dag.sh
```

This is specific to the incident repo, not a general recovery command. Other projects won't have `create-beads-dag.sh`. The recovery section should use a generic `git add -A` or explicitly note the script is incident-specific.

**Fix:** Replace with `git add plan_docs/` or add a note that `create-beads-dag.sh` is project-specific.

---

### Summary Table

| # | Severity | Category | Description |
|---|----------|----------|-------------|
| 1 | **Critical** | Technical bug | `test_poll_skips_project_with_untracked_plan` — `mock_empty_beads` patches the same `subprocess.run` that `_plan_tracked` uses; mock returns `returncode=0`, so the guard never fires and the test passes vacuously |
| 2 | **Critical** | Technical bug | `test_poll_processes_project_with_committed_plan` — uses undefined fixture `mock_beads_with_ready`, missing `import subprocess`, missing `GIT_COMMITTER_*` env vars |
| 3 | Significant | Gap | Log-spam mitigation (`_plan_warned` set) described in notes but absent from the implementation sketch code |
| 4 | Significant | Gap | `image/AGENTS.md` change listed in scope table with no implementation guidance or proposed text |
| 5 | Minor | Stale reference | `beads_loop.py` line numbers off by ~30 lines throughout (actual method starts at 146, not 115) |
| 6 | Minor | Redundant | `import subprocess` in implementation sketch duplicates existing import at `beads_loop.py:6` |
| 7 | Minor | Technical bug | `_poll_and_process_project` sketch signature drops `project_slug` parameter |
| 8 | Minor | Gap | Recovery one-liner includes incident-specific `create-beads-dag.sh` without noting it's project-specific |
