# Beads Worktree Missing Project Content — Root Cause Analysis

**Status:** Analysis complete. Fix not yet applied.
**Date:** 2026-07-01
**Symptom:** Per-bead agents fail with `File not found: .../plan_docs/application_plan.md`
and `external_directory` permission prompts that block in non-interactive runs.

## TL;DR

Per-bead git worktrees are created from the project repo's `main` branch, but
`plan_docs/application_plan.md` (and other project content) was **never committed** to
`main`. The worktree therefore checks out empty of real content, the agent cannot find
the plan it was told to read, and reaching outside the worktree to find it trips
`external_directory` permission asks that hang non-interactive `opencode run`.

Two skills violate the documented "the plan must be committed to `main`" contract:

1. **`plan-app`** (the current ideation skill) writes `application_plan.md` to disk but
   **never commits it** — a regression from its predecessor `perfect-idea`, which did.
2. **`plan-to-beads`** Step 5 commits **only** `.beads/` (`git add .beads/`), omitting
   `plan_docs/` and any other seeded project content.

## Evidence

### The project repo only has `.beads/` committed

```
$ cd /home/nam20485/orchestrator-workspace/session-20260629-232931-1b9ca4
$ git ls-files            # what is actually committed on main
.beads/.gitignore
.beads/config.yaml
.beads/issues.jsonl
.beads/metadata.json

$ git status --short      # the real content is untracked
?? create-beads-dag.sh
?? plan_docs/             # application_plan.md lives here, untracked

$ git log --oneline
3bce590 Add beads DAG for Gap-Miner application plan
```

Commit `3bce590` touched **only** `.beads/` (4 files). `plan_docs/application_plan.md`
(30 KB, on disk) was left untracked.

### The worktree inherits only committed content

`webhook_receiver/workspace.py:263` (`create_bead_worktree`) runs:

```python
subprocess.run(
    ["git", "worktree", "add", "-b", branch_name, wt_path, branch],   # branch = "main"
    ...
)
```

A worktree checks out the named base branch (`main`). Since `main` only contains
`.beads/` config, the worktree at `.worktrees/<bead>/` contains only `.beads/` — no
`plan_docs/`, no `README.md`, no source.

```
$ ls .worktrees/session-20260629-232931-1b9ca4-kdz/
AGENTS.md  BEADS_AGENT_GUIDE.md  .beads/  .git
```

(`AGENTS.md` / `BEADS_AGENT_GUIDE.md` appear only because opencode itself writes them —
see `bead_context.write_context_files` and the `"touching file"` server log lines. They
are **not** coming from git.)

### The runtime errors (two faces of one root cause)

1. **`File not found`** — `bead_context.read_project_overview()` (`bead_context.py:79`)
   reads `plan_docs/application_plan.md` from the **worktree** path (`ws_path`). The file
   is absent → the agent prompt's "READ `plan_docs/application_plan.md` FIRST" fails:

   ```
   ✗ Read .../.worktrees/.../plan_docs/application_plan.md failed
   Error: File not found
   ✗ Read .../.worktrees/.../README.md failed
   ```

2. **`external_directory` permission asks** — to find the missing plan, the agent escapes
   the worktree (`../../plan_docs/...`). That path is outside `--dir`, so opencode
   evaluates it as `external_directory` with `action=ask`:

   ```
   evaluated permission=external_directory pattern=/workspace/session-.../* action.action=ask
   message="asking id=per_... permission=external_directory ..."
   ```

   In non-interactive `opencode run --attach` (one-shot, no human to approve), `ask`
   prompts cannot be satisfied → the run stalls / fails.

## The Design Contract (what should be true)

The codebase **assumes** `plan_docs/application_plan.md` is committed to the project
repo's default branch before any bead runs:

- `tests/test_beads_loop.py:170-174` — *"A per-bead git worktree checks out the project
  repo, so if the plan is committed it is visible in the worktree."*
- `image/.opencode/skills/plan-to-beads/SKILL.md:20-26` — *"commits to the project's
  `main` branch (including `plan_docs/application_plan.md` and `.beads/`) are visible to
  all bead agents."*
- `bead_context.read_project_overview()` reads exclusively from the worktree path.

Nothing enforces this contract. Both ideation skills and the planning skill rely on it
being true but neither guarantees it.

## The Deeper Bug — Skill Commit Gaps

### 1. `plan-app` dropped the commit step (regression)

`image/.opencode/skills/plan-app/SKILL.md` is the current ideation skill ("Supersedes
/perfect-idea"). Its final step ("Handoff", lines ~132-137) writes
`plan_docs/application_plan.md` to disk and tells the user to run `/plan-to-beads` — but
it contains **no `git add` / `git commit`** anywhere (`grep -niE "git|commit|push" → no
matches`).

Its predecessor `perfect-idea/SKILL.md:43-44` **did** commit:

```bash
git add plan_docs/application_plan.md
git commit -m "Add application plan"
```

When `plan-app` superseded `perfect-idea`, the commit step was lost. So plans generated
via `/plan-app` are written but never committed.

### 2. `plan-to-beads` commits only `.beads/`

`image/.opencode/skills/plan-to-beads/SKILL.md` Step 5 ("Execute and Commit", lines
63-72):

```bash
br sync --flush-only || { echo "ERROR: br sync failed"; exit 1; }
br sync --status | grep -q "In sync" || { echo "ERROR: beads not in sync"; exit 1; }
git add .beads/
git commit -m "Add beads DAG from application plan"
```

It stages **only** `.beads/`. Even if `plan_docs/application_plan.md` had been committed
upstream by `perfect-idea`, the `plan-to-beads` commit is the natural safety net — and
it does not include `plan_docs/`. (In this incident the plan was untracked because
`plan-app` was used, so the net caught nothing.)

### Why this manifests only now

- Non-root execution (commit `14f0e16`) and the multi-project worktree model are recent.
  Earlier single-project layouts placed the agent's working dir directly on the repo
  root, so untracked files were incidentally visible. The per-bead **worktree** model
  (`workspace.py`) makes the "must be committed" contract load-bearing — untracked files
  no longer leak into bead execution.
- `read_project_overview` was changed to source from the worktree (`ws_path`) rather than
  the project root (see `test_build_prompt_overview_from_canonical_root`), cementing the
  contract. The skills were not updated to match.

## Impact

- Every bead agent spawned for a project whose plan was generated via `/plan-app` (or any
  flow that doesn't commit the plan) starts in an empty worktree, cannot read the plan,
  and either fails fast (`File not found`) or stalls on `external_directory` asks.
- `BEADS_AGENT_GUIDE.md` is degraded: `read_project_overview` returns *"(No
  application_plan.md found in this workspace.)"*.
- The first bead ("Repository bootstrap") may limp through because its job is to create
  files, but any bead that depends on prior project content (README, source, plan) is
  broken.

## Recommended Fix (not yet applied)

Three layers, in priority order: **A** fixes the root cause in the skills; **B** adds a
defensive loop-level guard so the contract can never silently break again; **C** recovers
existing broken projects. All three are independently valuable; **A.1** alone unblocks the
current incident.

### A. Fix the skills (root cause)

#### A.1 `plan-to-beads/SKILL.md` Step 5 — commit the plan alongside the DAG  *(highest value)*

This is the single most important change because `plan-to-beads` is the last gate before
`BeadsLoop` picks up work. Even if upstream forgot to commit the plan, this step catches
it.

**Current** (`image/.opencode/skills/plan-to-beads/SKILL.md`, Step 5 "Execute and
Commit", lines 63-72):

```bash
br sync --flush-only || { echo "ERROR: br sync failed"; exit 1; }
br sync --status | grep -q "In sync" || { echo "ERROR: beads not in sync"; exit 1; }
git add .beads/
git commit -m "Add beads DAG from application plan"
```

**Proposed:**

```bash
br sync --flush-only || { echo "ERROR: br sync failed"; exit 1; }
br sync --status | grep -q "In sync" || { echo "ERROR: beads not in sync"; exit 1; }

# Per-bead worktrees check out `main`; only COMMITTED files are visible to bead agents.
# Stage the plan (and any other seeded project docs) alongside the DAG so worktrees
# inherit them. Without this, bead agents run in an empty worktree and cannot read
# plan_docs/application_plan.md.
git add plan_docs/ .beads/
git commit -m "Add application plan and beads DAG"
```

Also update the `<prerequisites>` block (lines 20-26) and `<example_script>` (lines
125-128) so the documented commit matches — currently the example script ends at
`br sync --status` with no `git add`/`commit` at all, which is a second inconsistency.

#### A.2 `plan-app/SKILL.md` — restore the commit step `perfect-idea` had  *(defense in depth)*

`plan-app` ("Supersedes /perfect-idea") dropped the commit that `perfect-idea/SKILL.md:43-44`
performed. Restore it so the plan is committed at generation time, not just at planning
time.

**Current** (`image/.opencode/skills/plan-app/SKILL.md`, "Generate the final plan doc" →
"Handoff", lines 128-138): writes the file, then immediately hands off — **no commit**.

**Proposed** — insert a commit step between "Generate the final plan doc" (step 2) and
"Handoff" (step 3):

```bash
git add plan_docs/application_plan.md
git commit -m "Add application plan"
```

(Alternatively, fold it into step 2's "Write the completed document…". Either way the
skill must not leave `application_plan.md` untracked.)

### B. Defensive guard in `BeadsLoop`  *(recommended — decouples correctness from skill discipline)*

Add a cheap precondition check that runs **before** a project's first bead is processed:
verify `plan_docs/application_plan.md` is **tracked** in the project repo. If it is missing
or untracked, log a prominent ERROR once and skip the project for that scan — do **not**
spawn agents into empty worktrees. This turns a silent, confusing per-agent failure into a
single operator-actionable message at the loop level.

**Plug-in point:** `webhook_receiver/beads_loop.py`, at the top of
`_poll_and_process_project` (line 146), right before `_get_next_bead` (line 150). Guarding
there covers both the scan path (`_scan_and_process`, line 133) and any direct caller.

**Implementation sketch** (new module-level helper + one guard call):

```python
# webhook_receiver/beads_loop.py  (subprocess is already imported at line 6)

_PLAN_REL = "plan_docs/application_plan.md"


def _plan_tracked(project_root: str) -> bool:
    """True iff the application plan is tracked in the project repo's index/HEAD.

    Per-bead worktrees check out the default branch, so only tracked files reach bead
    agents. An untracked plan means worktrees will be empty of project content.
    """
    try:
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", _PLAN_REL],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return res.returncode == 0
```

```python
# inside _poll_and_process_project (beads_loop.py:146), before _get_next_bead:
def _poll_and_process_project(self, project_slug: str, project_root: str) -> None:
    """Poll one project for the next bead and process it."""
    if not _plan_tracked(project_root):
        if project_slug not in self._plan_warned:
            logger.error(
                "Skipping project=%s: %s is not committed to git. Per-bead worktrees "
                "would be empty. Run the ideation/planning skill's commit step, then "
                "this project will be picked up automatically.",
                project_slug,
                _PLAN_REL,
            )
            self._plan_warned.add(project_slug)
        return
    self._plan_warned.discard(project_slug)  # reset on recovery

    bead = self._get_next_bead(project_root)
    ...
```

Also add to `__init__`:
```python
self._plan_warned: set[str] = set()
```

Notes:
- Use `git ls-files --error-unmatch` (checks the **index**, i.e. staged/tracked) rather
  than testing file existence on disk — the whole point is that the file exists on disk
  but is *untracked*, which is exactly the failure mode here.
- `_plan_warned` prevents log spam on every 10 s poll; `discard` resets when the plan
  is committed and the project recovers.
- This guard does **not** auto-commit — it surfaces the problem. Auto-committing from the
  loop would mask skill bugs and could commit partial/hallucinated content.

### C. Existing-project recovery (operator one-liner)

For projects already in the broken state (plan untracked, empty worktrees already
created), commit the plan and recreate the stale worktree so the loop rebuilds it from the
corrected `main`:

```bash
cd <project_root>   # e.g. /home/nam20485/orchestrator-workspace/session-...
git add plan_docs/  # commit the plan (and any other untracked project content)
git commit -m "Add application plan"
# remove the stale empty worktree so BeadsLoop recreates it from updated main
git worktree remove --force .worktrees/<bead-id>
git branch -D task/<bead-id>
```

After commit `14f0e16` (non-root execution), also ensure the workspace isn't root-owned
(separate but related startup error): `sudo chown -R 1000:1000 $WORKSPACE_DIR`.

## Verification Plan (after fix is applied)

**Manual / integration:**

1. Run `/plan-app` then `/plan-to-beads` against a fresh session; confirm
   `git ls-files` includes `plan_docs/application_plan.md` (i.e. the plan is tracked, not
   just present on disk).
2. `git worktree add /tmp/wt main && ls /tmp/wt/plan_docs/` — confirm the plan is present
   in a worktree checked out from `main`.
3. Start the stack (`docker compose -f compose.development.yaml up`); confirm
   `BEADS_AGENT_GUIDE.md` contains the real project overview (not the *"(No
   application_plan.md found in this workspace.)"* fallback) and that no
   `external_directory` `action=ask` prompts appear in the orchestrator logs.
4. For a deliberately-broken project (untracked plan), confirm the guard logs a single
   ERROR and skips dispatch (no agent spawned, no empty worktree created).

**Automated tests to add (`tests/test_beads_loop.py`):**

*Note: `mock_empty_beads` patches `webhook_receiver.beads_loop.subprocess.run` — the exact
symbol `_plan_tracked()` also calls. Using it here would make `_plan_tracked()` always
return `True` (mock returns `returncode=0`). Patch `_plan_tracked` directly instead.*

```python
import subprocess

from unittest.mock import patch


def test_poll_skips_project_with_untracked_plan(tmp_path, mock_empty_beads):
    """Guard skips a project whose application_plan.md is untracked.

    Per-bead worktrees check out main; an untracked plan means worktrees would be
    empty. The loop must refuse to dispatch and log an error instead.
    """
    # Patch _plan_tracked directly — mock_empty_beads would mask the git call
    with patch("webhook_receiver.beads_loop._plan_tracked", return_value=False):
        settings = _test_settings(beads_workspace_root=str(tmp_path))
        loop = BeadsLoop(settings)
        loop._poll_and_process_project("proj", str(tmp_path / "proj"))

        # No bead dispatched, no worktree created
        assert loop._active_beads == set()


def test_poll_processes_project_with_committed_plan(tmp_path):
    """A project whose plan IS committed is dispatched normally.

    Uses real git (no mock_empty_beads) so _plan_tracked() sees the committed file.
    """
    import os

    project = tmp_path / "proj"
    (project / "plan_docs").mkdir(parents=True)
    (project / "plan_docs" / "application_plan.md").write_text("# plan", encoding="utf-8")
    (project / ".beads").mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=project, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "plan_docs/application_plan.md"], cwd=project,
                   check=True, capture_output=True)
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "commit", "-m", "plan"], cwd=project,
                   check=True, capture_output=True, env=git_env)

    settings = _test_settings(beads_workspace_root=str(tmp_path))
    loop = BeadsLoop(settings)
    # _plan_tracked should return True for a committed plan (real git call)
    from webhook_receiver.beads_loop import _plan_tracked
    assert _plan_tracked(str(project)) is True
```

These mirror the existing `test_build_prompt_overview_from_canonical_root`
(`tests/test_beads_loop.py:167`) which already encodes the "committed plan is visible in
the worktree" contract.

## Scope of Changes (when approved)

| Layer | File(s) | Change |
|-------|---------|--------|
| A.1 | `image/.opencode/skills/plan-to-beads/SKILL.md` | Step 5: `git add plan_docs/ .beads/`; align `<example_script>` and `<prerequisites>` |
| A.2 | `image/.opencode/skills/plan-app/SKILL.md` | Insert `git add` + `git commit` between plan generation and handoff |
| B | `webhook_receiver/beads_loop.py` | `_plan_tracked()` helper + `_plan_warned` set + guard at top of `_poll_and_process_project` (line 146) |
| B | `tests/test_beads_loop.py` | Add the two guard tests above |

*Follow-up (not in scope for this fix):* Document the "plan must be committed to `main`
before beads run" contract as a rule in `image/AGENTS.md`. Defer until the skill fixes
(Layer A) land, since the rule is self-evident once the skills commit correctly.

No runtime-config, Dockerfile, or compose changes are required — this is purely a
skill-discipline + loop-guard fix.

## Related

- `webhook_receiver/workspace.py:229-270` — `create_bead_worktree` (worktree checks out
  `main`; only tracked files reach bead agents).
- `webhook_receiver/bead_context.py:74-92` — `read_project_overview` (reads the plan from
  the **worktree** path `ws_path`, not the project root).
- `webhook_receiver/beads_loop.py:146-162` — `_poll_and_process_project`, the guard's
  plug-in point.
- `webhook_receiver/beads_loop.py:374-409` — `_build_bead_prompt` which tells the agent to
  "READ `plan_docs/application_plan.md` FIRST".
- `image/.opencode/skills/plan-to-beads/SKILL.md:63-72` — Step 5 commit (`git add .beads/`
  only).
- `image/.opencode/skills/plan-to-beads/SKILL.md:20-26` — `<prerequisites>` stating the
  contract the skill then violates.
- `image/.opencode/skills/plan-app/SKILL.md:128-138` — generation + handoff with no commit
  (regression).
- `image/.opencode/skills/perfect-idea/SKILL.md:43-44` — the commit step `plan-app` lost.
- `tests/test_beads_loop.py:167-191` — `test_build_prompt_overview_from_canonical_root`,
  the documented "committed plan is visible in the worktree" contract.
