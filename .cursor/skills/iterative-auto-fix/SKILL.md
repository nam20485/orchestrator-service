---
name: iterative-auto-fix
description: >-
  Iteratively and autonomously fix a defect or implement a feature by
  determining a solution, implementing it, adding tracing, running the
  program in automated fix mode, then inspecting logs after it exits.
  Repeats the loop until the goal is met. Use when the user asks to
  "auto-fix", "iteratively fix", "debug this automatically",
  "implement and verify", or any request for autonomous iterative
  debugging and implementation.
---

# Iterative Auto-Fix

Autonomously iterate through **analyze → plan → implement → trace → run → inspect** until a defect is fixed or a feature is implemented and verified.

## Prerequisites

- User provided a problem description or feature request
- The codebase is accessible in the current workspace
- The program can be built and run (or the user can tell you how)

## Phase 0 — Goal Classification

Parse the user's request and classify:

| Mode | Trigger | Success Criteria |
|------|---------|-----------------|
| **Fix defect** | "X is broken", "Y shouldn't happen", "Z fails" | Specific unwanted behavior **stops** |
| **Implement feature** | "add X", "implement Y", "make Z work" | Specific new functionality **starts** and is observable |

Define **concrete, testable** success criteria before proceeding:

```markdown
## Goal

**Mode:** Fix defect | Implement feature
**Description:** <what the user wants>
**Success criteria:**
- [ ] <testable condition 1>
- [ ] <testable condition 2>
**Relevant area:** <files, modules, components>
```

Present the goal to the user for confirmation before starting the loop.

## Phase 1 — Automation Infrastructure

Before any debugging, ensure the program can run the target functionality **headlessly** (no UI, no manual interaction).

### 1.1 Discover existing automation

Check for:

- CLI arguments that trigger headless/automated mode (e.g., `--auto`, `--headless`, `--test-mode`)
- Test harnesses that exercise the functionality (e.g., `pytest`, `dotnet test`, `go test`)
- Scripts that run the program non-interactively
- Environment variables that enable automation mode

### 1.2 Add automation if missing

If no automation exists, add **minimal** support:

1. **CLI argument** to run the specific functionality and exit:
   - Name it descriptively: `--auto-<action>` (e.g., `--auto-render`, `--auto-dispatch`, `--auto-process`)
   - Accept parameters needed to exercise the target code path
   - Exit cleanly after the functionality completes (non-zero on failure)

2. **Structured tracing** using the project's existing logging framework:
   - If the project uses a logger (e.g., `ILogger`, `logging`, `log/slog`), use it
   - If no logger exists, add stderr output with structured tags
   - Use `LogCritical` / `ERROR` / `eprintln!` level for diagnostic traces to ensure visibility

3. **Clean exit**: The automated run must exit on its own (no hanging, no waiting for input)

### 1.3 Baseline run

Run the automation once **before** making any changes to establish a baseline:

```bash
<build command> && <run command with automation args> 2>&1 | tee debug-output-baseline.txt
```

Verify:
- The program builds successfully
- The automated mode runs and exits
- Output is captured

## Phase 2 — The Iterative Loop

Each iteration follows: **Analyze → Plan → Implement → Build & Run → Inspect → Decide**

### Iteration tracking

Create a tracking document at `plan_docs/iterative-fix-<topic>/iteration-log.md`:

```markdown
# Iteration Log: <topic>

**Goal:** <from Phase 0>
**Started:** <timestamp>

---

## Iteration N

**Date:** <date>
**Hypothesis:** <what you think is wrong or what needs to happen>
**Changes:** <files modified, tracing added>
**Run command:** <exact command used>
**Log file:** `debug-output-iterN.txt`

### Findings
- <finding 1>
- <finding 2>

### Root Cause / Blocker
<identified or not yet>

### Next Action
<what to do in the next iteration>
```

### Iteration 1: Entry Point Verification

**Goal:** Confirm the code paths involved in the problem are actually executing.

**Actions:**

1. Identify the key functions/methods involved in the problem
2. Add entry point tracing at the very start of each:

```
[ComponentName] ENTRY: <function> called with <key params>
```

Use the highest-visibility log level to ensure output even if log config is wrong:
- C#/.NET: `_logger.LogCritical(...)`
- Python: `logger.critical(...)` or `print(..., file=sys.stderr)`
- Go: `log.Printf("[CRITICAL] ...")`
- Rust: `eprintln!("[CRITICAL] ...")`
- JavaScript/TypeScript: `console.error("[CRITICAL] ...")`

3. Build and run in automated mode:

```bash
<build> && <run with automation args> 2>&1 | tee debug-output-iter1.txt
```

4. Inspect output:

**If entry point traces appear:**
- ✅ Code paths are reached
- ✅ Logging infrastructure works
- ✅ Proceed to Iteration 2

**If entry point traces do NOT appear:**
- ❌ Code not reached — investigate why (wrong entry point? build issue? wrong automation args?)
- ❌ Logging misconfigured — fix log level/config
- Fix the blocker before proceeding

### Iteration 2+: Targeted Diagnostics

**Goal:** Add progressively deeper tracing based on previous iteration's findings.

**Strategy — trace at every decision point:**

| What to trace | Example tag |
|---------------|-------------|
| Conditionals and branches | `[Auth][ITER2] BRANCH: token present={true}` |
| Data lookups | `[Cache][ITER2] LOOKUP: key={key}, found={false}` |
| Error paths | `[RPC][ITER2] FAIL: code={NotFound}, detail={...}` |
| Data transformations | `[Parse][ITER2] INPUT: raw={...}, OUTPUT: parsed={...}` |
| State changes | `[State][ITER2] TRANSITION: from={idle}, to={running}` |
| Null/empty checks | `[Data][ITER2] NULL_CHECK: field={name}, isNull={true}` |

**Log tag format:** `[Component][ITER_N] <EVENT>: <details>`

This format enables grep-based filtering:

```bash
# Filter by component
grep "\[Auth\]" debug-output-iterN.txt

# Filter by iteration
grep "\[ITER2\]" debug-output-iter2.txt

# Filter failures
grep "FAIL" debug-output-iterN.txt

# Filter specific events
grep "LOOKUP" debug-output-iterN.txt
```

**Each iteration:**

1. **Analyze** previous iteration's logs — what was learned? What's still unknown?
2. **Plan** what to trace next — go one level deeper into the call stack or data flow
3. **Implement** the new tracing (and any fix if root cause is identified)
4. **Build & Run** with log capture
5. **Inspect** the new logs
6. **Decide:**
   - Root cause identified → implement fix, proceed to verification
   - Need more depth → add more tracing, iterate again
   - Fix applied → verify, proceed to Phase 3

### Decision Tree

```
                    ┌─ Entry points reached? ─┐
                    │                         │
                   YES                        NO
                    │                         │
            Add deeper tracing        Fix automation/
                    │                 build/reachability
            What do logs show?              │
                    │                  Re-run iter 1
        ┌───────────┼───────────┐
        │           │           │
    Error path   Data issue   Logic error
        │           │           │
    Fix error   Fix data    Fix logic
    handling    flow        /condition
        │           │           │
        └───────────┴───────────┘
                    │
            Add verification tracing
                    │
            Run automated mode
                    │
            Goal met? ──NO──→ Iterate again
                    │
                   YES
                    │
            Phase 3: Cleanup
```

## Phase 3 — Fix Verification

Once a fix is implemented:

1. **Add verification tracing** that confirms the fix works:
   - Trace the corrected behavior explicitly
   - Trace that the old broken behavior no longer occurs

2. **Run automated mode** and verify:
   - Success criteria from Phase 0 are met
   - No new errors introduced
   - Verification traces show expected values

3. **If verification fails:**
   - Analyze what went wrong
   - Return to Phase 2 with new hypothesis
   - Add tracing to understand why the fix didn't work

## Phase 4 — Cleanup

After successful verification:

1. **Remove or downgrade diagnostic tracing:**
   - Remove `LogCritical`/`console.error` diagnostic traces
   - Keep production-appropriate logging at appropriate levels (`Debug`, `Info`)
   - Remove iteration tags (`[ITER_N]`)

2. **Keep the automation CLI arg** — it's useful for future debugging and CI

3. **Run final verification** to confirm cleanup didn't break anything:

```bash
<build> && <run with automation args> 2>&1 | tee debug-output-final.txt
```

4. **Verify success criteria** from Phase 0 one last time

## Phase 5 — Report

Present the final summary:

```markdown
## Iterative Auto-Fix Complete

**Goal:** <description>
**Mode:** Fix defect | Implement feature
**Iterations:** N
**Status:** ✅ Success | ⚠️ Partial | ❌ Blocked

### Root Cause
<what was actually wrong, or what was needed for the feature>

### Fix / Implementation
<what was changed, files modified>

### Verification
<evidence from automated run that goal is met>

### Files Modified
- `path/to/file1` — <what changed>
- `path/to/file2` — <what changed>

### Iteration Log
`plan_docs/iterative-fix-<topic>/iteration-log.md`

### Remaining Work (if any)
- <any follow-up items>
```

## Failure Handling

| Situation | Action |
|-----------|--------|
| Build fails | Fix build errors before continuing; do not skip |
| Automated mode hangs | Add timeout; investigate what's blocking; add tracing before the hang point |
| Logs show no output | Check logging configuration; try stderr directly; verify the code path is reached |
| Root cause unclear after 5 iterations | Escalate to user with findings so far; propose hypotheses |
| Fix doesn't resolve the issue | Revert fix, add more tracing, iterate with new hypothesis |
| New errors appear after fix | Address new errors as a new iteration; don't ignore regressions |
| Max iterations (10) reached | Stop, present all findings, ask user for direction |

## Quality Checklist

Before declaring success, verify:

- [ ] Success criteria from Phase 0 are demonstrably met (from log evidence, not assumption)
- [ ] Automated run exits cleanly (no hangs, no crashes)
- [ ] No new errors in the automated run output
- [ ] Diagnostic tracing cleaned up (no `LogCritical` debug traces left in production code)
- [ ] Automation CLI arg still works after cleanup
- [ ] Iteration log is complete (all iterations documented)
- [ ] Build succeeds cleanly

## Guidelines

### Tracing best practices

- **Tag everything**: `[Component][ITER_N]` format for grep-friendly filtering
- **Log values, not just events**: `key={value}` not just `key checked`
- **Log both paths**: trace both the `if` and `else` branches so you can see which was taken
- **Log before AND after**: trace input before a call, output after
- **Use structured format**: `KEY=VALUE` pairs, not free-form text
- **Include context**: thread ID, timestamp, sequence number when relevant

### Automation CLI design

- Prefix with `--auto-` to distinguish from normal args
- Accept all parameters needed to exercise the target path
- Support `--trace` or `--verbose` flag for extra diagnostic output
- Exit code 0 = success, non-zero = failure
- Print summary to stderr before exit

### When to stop iterating

- **Goal met**: success criteria verified from log evidence → cleanup and report
- **Blocked**: cannot proceed without external input (e.g., server-side change, API key) → report with clear blocker description
- **Diminishing returns**: 5+ iterations with no new information → escalate to user
- **Scope creep**: fix reveals a different, larger problem → report findings, let user decide

## Example Invocations

### Fix a defect

```
User: /iterative-auto-fix "webhook receiver drops events when payload exceeds 1MB"

Agent: [Phase 0] Mode: fix defect. Success: payloads > 1MB processed without drops.
Agent: [Phase 1] Discovers FastAPI test client. Adds --auto-webhook-test CLI arg.
Agent: [Phase 2, Iter 1] Entry point tracing on webhook handler. Handler reached.
Agent: [Phase 2, Iter 2] Payload size tracing. Found: Caddy body limit at 1MB.
Agent: [Phase 3] Fix: increase Caddy body limit. Verification: 5MB payload processed.
Agent: [Phase 4] Cleanup: downgrade traces to Debug level.
Agent: [Phase 5] Report: 3 iterations, root cause = Caddy body limit, fixed.
```

### Implement a feature

```
User: /iterative-auto-fix "add exponential backoff retry to bead dispatch"

Agent: [Phase 0] Mode: implement feature. Success: failed dispatches retry with backoff.
Agent: [Phase 1] Discovers pytest harness. Adds --auto-dispatch-test with --fail-count.
Agent: [Phase 2, Iter 1] Entry point tracing on dispatch. Dispatch path confirmed.
Agent: [Phase 2, Iter 2] Retry loop tracing. Found: no retry logic exists.
Agent: [Phase 3] Implement: retry with exponential backoff (3 attempts).
Agent: [Phase 2, Iter 3] Verification tracing confirms retries fire on failure.
Agent: [Phase 4] Cleanup: keep retry logging at Info level.
Agent: [Phase 5] Report: 4 iterations, feature implemented and verified.