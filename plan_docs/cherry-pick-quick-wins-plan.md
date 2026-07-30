# Cherry-pick 4 commits from `dev/agent-readiness-quick-wins` to `nam20485`

## Goal
Selectively apply 4 useful commits from `dev/agent-readiness-quick-wins` onto a new review branch cut from `nam20485`, leaving the 9 Factory AI readiness commits behind.

## Branch Topology
- `dev/agent-readiness-quick-wins` was cut from `nam20485` (merge-base: `9c5a969`)
- 13 commits ahead of `nam20485`; 4 wanted, 9 to discard

## Commits to Cherry-pick (chronological order)
| # | SHA | Message |
|---|-----|---------|
| 1 | `11395f7` | feat: add container HEALTHCHECKs and gate webhook-receiver on orchestratorservice health |
| 2 | `797c323` | feat: export FastAPI OpenAPI schema as a committed artifact |
| 3 | `dc4118f` | ci(workflows): pin droid-action version and configure review models |
| 4 | `4cc61dd` | ci: use forked droid-action with empty-default input fix |

## Dependency Analysis

### Known Conflict: `scripts/validate.ps1`
- Skipped commit `d9cc065` renamed `'ruff'` → `'ruff check'` and added `ruff format --check` block
- Wanted commit `797c323` appends `scripts/export-openapi.py` to those ruff lines, expecting the `d9cc065` names
- On `nam20485`, the lines are still `Invoke-ValidateStep -Name 'ruff'`
- **Resolution**: when cherry-pick conflicts, apply the `scripts/export-openapi.py` addition to the existing `nam20485` ruff lines (keep name `'ruff'`, just append the path)

### No Conflict: `scripts/validate.ps1` test section
- `11395f7` adds `Invoke-BashStep -Name 'docker healthchecks'` in the test scripts section (~line 137)
- `797c323` adds `Invoke-BashStep -Name 'openapi schema'` right after
- This area is untouched by `d9cc065`, so context matches cleanly

### No Conflict: `.github/workflows/droid-review.yml`
- Skipped `e84c03c` only removes trailing whitespace between `dc4118f` and `4cc61dd`
- `4cc61dd` builds on `dc4118f`'s content, not the whitespace fix — safe to skip

### No Conflict: all other files
- `compose.yaml`, `Dockerfile`, `Dockerfile.webhook`, `deploy/caddy/Dockerfile`, test scripts, `docs/openapi.json`, `scripts/export-openapi.py`, workflow files — no overlaps with skipped commits

## Implementation Steps

1. **Create branch**: `git checkout nam20485 && git checkout -b cherry-pick/quick-wins-selection`

2. **Cherry-pick in order**:
   ```
   git cherry-pick 11395f7   # HEALTHCHECKs — expect clean apply
   git cherry-pick 797c323   # OpenAPI schema — expect conflict in validate.ps1
   ```

3. **Resolve conflict** in `scripts/validate.ps1`:
   - Keep the `nam20485` ruff line names (`'ruff'` not `'ruff check'`)
    - Append `scripts/export-openapi.py` to the existing `uv run ruff check` line (no `ruff format` step exists on `nam20485` — commit `d9cc065` was skipped)
   - The `Invoke-BashStep -Name 'openapi schema'` line should apply cleanly
   - `git add scripts/validate.ps1 && git cherry-pick --continue`

4. **Cherry-pick remaining**:
   ```
   git cherry-pick dc4118f   # pin droid-action — expect clean apply
   git cherry-pick 4cc61dd   # forked droid-action — expect clean apply
   ```

5. **Validate**:
   - `git log --oneline` — confirm 4 commits applied cleanly
   - `git diff nam20485` — review all changes are isolated to the 4 commits' scope
   - Confirm no Factory AI readiness files leaked (no `agent-readiness-action-plan.md`, no ruff format changes to `webhook_receiver/*.py`, no Sentry, no rollback overlay, no dependabot)

6. **Push and PR**: push branch and open PR against `nam20485`

## Risk Assessment
- **Low risk**: only 4 well-scoped commits; single predictable conflict with known resolution
- **No risk to `nam20485`**: work happens on an isolated branch
- **Rollback**: delete the branch if the PR review surfaces issues
