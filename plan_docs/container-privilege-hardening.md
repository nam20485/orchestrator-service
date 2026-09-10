# Plan: Fix Issues from gap-miner-v2-alpha61 Run Report

## Goal
Fix the 4 actionable issues discovered during the first successful end-to-end `gh-issue-tracking-init` run. Two issues are out of scope (subagent logging = architectural limitation; memory-graph MCP failure = transient).

## Issues

| # | Issue | Repo | Severity |
|---|-------|------|----------|
| 1 | `gh auth status` denied by bash permission allowlist | `intel-agency/agent-context` (template) | Low |
| 2 | `task_id` format error — no agent guidance | `orchestrator-service` (this repo) | Low |
| 3 | GLM-5.2 used by subagents instead of GLM-5 | `intel-agency/agent-context` (template) | Medium |
| 5 | `link-sub-issue.ps1` / `set-dependency.ps1` REST probe before DryRun guard | `intel-agency/agent-context` (template) | Low |

Issues 4 (subagent logging) and 6 (memory-graph MCP failure) are out of scope.

---

## Task 1: Add `gh auth*` to bash permission allowlist

**File:** `intel-agency/agent-context` template repo — `.opencode/opencode.jsonc`

**Problem:** The opencode v1.18.4 server applies a built-in bash allowlist for headless mode that includes `gh pr*`, `gh issue*`, `gh repo view*`, `gh run*` but not `gh auth*`. The orchestrator's `gh auth status` call is denied.

**Fix:** Add `gh auth` to the project config's permission object:
```jsonc
"permission": {
    "websearch": "allow",
    "bash": "gh auth*"
},
```

**Alternative:** If the project config doesn't support bash pattern strings, add it to the orchestrator agent frontmatter in `image/.opencode/agents/orchestrator.md` (this repo):
```yaml
permission:
  bash: deny
  bash_allow:
    - "gh auth*"
```

**Validation:** Run a test dispatch and verify `gh auth status` succeeds.

**Note:** The orchestrator already works around this by using `gh repo view` instead. This fix is a quality-of-life improvement, not a blocker.

---

## Task 2: Add `task_id` guidance to orchestrator agent definition

**File:** `image/.opencode/agents/orchestrator.md` (this repo)

**Problem:** The orchestrator tried to pass `task_id="gh-init-phase1"` to the task tool, which requires a session ID (must start with "ses"). The orchestrator retried without it and lost the ability to resume the session for Phase 2.

**Fix:** Add a section to the orchestrator agent definition explaining `task_id` usage:

```markdown
## Task Tool — Session Resumption

The `task` tool's `task_id` parameter is for resuming an existing subagent session. It MUST be a session ID returned by a previous `task` call (format: `ses_...`). Do NOT pass custom strings like `"gh-init-phase1"` — the tool will reject them.

To resume a session for a follow-up phase:
1. Capture the `task_id` from the first delegation's result
2. Pass it as `task_id` in the next `task` call to the same subagent
3. The subagent retains its full context from the previous phase

If you don't need to resume (fresh delegation), omit `task_id` entirely.
```

**Validation:** Run `bash test/test-opencode-json.sh` and verify the agent definition parses correctly.

---

## Task 3: Fix GLM-5.2 model in template project config

**File:** `intel-agency/agent-context` template repo — `.opencode/opencode.jsonc`

**Problem:** The project config has `"model": "zai-coding-plan/glm-5.2"`, which overrides the global config's `"model": "zai-coding-plan/glm-5"`. Subagents inherit from the session, which uses the project config model. The developer subagent ran on `glm-5.2` instead of `glm-5`.

**Fix:** Change line 5 of the project config:
```jsonc
"model": "zai-coding-plan/glm-5",
```

**Also check:** The seeding script in `nam20485/workflow-launch2` (`scripts/create-repo-agent-context.ps1`) may need to override the model post-clone. Per the project decision `glm5_agent_models_post_clone_orchestrator_only`, the model normalization should be applied post-clone. Verify the seeding script sets the model correctly.

**Validation:** Run a test dispatch and verify the developer subagent logs show `modelID=glm-5` (not `glm-5.2`).

---

## Task 4: Reorder DryRun guard in link-sub-issue.ps1 and set-dependency.ps1

**Files:** `intel-agency/agent-context` template repo:
- `.agents/skills/gh-issue-tracking-init/scripts/link-sub-issue.ps1`
- `.agents/skills/gh-issue-tracking-init/scripts/set-dependency.ps1`

**Problem:** Both scripts perform a REST idempotency probe (checking if the link/dep already exists via `gh api`) BEFORE the `-DryRun` guard. In DryRun mode with synthetic issue numbers, the probe would 404 because the issues don't exist yet. This means DryRun cannot fully validate link/dep operations.

**Fix:** Move the `if ($DryRun)` check before the REST probe in both scripts.

**link-sub-issue.ps1** — move lines 68-71 before lines 54-61:
```powershell
# BEFORE (current): probe first, then DryRun guard
# AFTER (fixed): DryRun guard first, then probe

if ($DryRun) {
    Write-DryRun "Would add #$ChildNumber as a sub-issue of #$ParentNumber."
    return
}

# Idempotency: is the child already a sub-issue of the parent?
$already = $false
try {
    $subs = Invoke-GhJson api "repos/$Repo/issues/$ParentNumber/sub_issues" --paginate
    if ($subs) { $already = [bool](@($subs | Where-Object { [int]$_.number -eq $ChildNumber }).Count) }
}
catch {
    throw "Failed to list existing sub-issues of #${ParentNumber} during discovery: $($_.Exception.Message)"
}

if ($already) {
    Write-Skip "#$ChildNumber is already a sub-issue of #$ParentNumber."
    return
}
```

**set-dependency.ps1** — same pattern: move `if ($DryRun)` before the REST probe.

**Validation:** Run the skill's DryRun with synthetic issue numbers and verify no 404 errors from the probe.

---

## Out of Scope

- **Issue 4 (subagent activity logging):** Architectural limitation of opencode v1.18.4 headless subagents. The developer's LLM output is only visible after it returns to the orchestrator. No fix available without an opencode server update.
- **Issue 6 (memory-graph_create_relations MCP failure):** Transient MCP server issue. Non-critical — observations were persisted. No code fix needed.

---

## Execution Order

1. **Task 2** (this repo) — add task_id guidance to orchestrator.md
2. **Task 1** (template repo) — add gh auth to bash allowlist
3. **Task 3** (template repo) — fix model to glm-5
4. **Task 4** (template repo) — reorder DryRun guards in skill scripts

Tasks 1, 3, 4 are in the template repo and can be done in a single PR there. Task 2 is in this repo and can be done independently.

## Validation

After all fixes:
1. Run `validate.ps1 -All` in this repo (for Task 2)
2. Run a test dispatch against a fresh clone and verify:
   - `gh auth status` succeeds (Task 1)
   - Developer subagent shows `modelID=glm-5` (Task 3)
   - DryRun with synthetic numbers produces no 404 errors (Task 4)
   - `task_id` resumption works for Phase 2 (Task 2)
