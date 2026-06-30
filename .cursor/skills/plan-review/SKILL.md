---
name: plan-review
description: >-
  Analyze a plan or specification document by cross-referencing every claim
  against the actual codebase. Finds contradictions, gaps, stale references,
  technical bugs, missing edge cases, and unresolved decisions. Produces a
  structured markdown review with severity-rated findings, then offers to apply
  fixes. Use when the user asks to review a plan, spec, design doc, POR, or
  implementation plan — or says "review this plan", "find issues in this spec",
  "analyze this document", "plan review".
---

# Plan Review

Systematically analyze a plan/specification document by cross-referencing every technical claim against the actual codebase. Produce a structured review with severity-rated findings, then offer to apply fixes.

## Prerequisites

- User provided a plan/spec document path (or the document content directly)
- The codebase referenced by the plan is accessible in the current workspace
- Do **not** modify any files until the user approves the review

## Phase 1 — Read the plan document

Read the entire plan document. Extract and catalog:

1. **File references** — every file path mentioned in the document (Dockerfiles, compose files, scripts, source code, configs, tests, docs)
2. **Technical claims** — assertions about what files contain, how they behave, what patterns they use
3. **Design decisions** — choices with stated rationale
4. **Task breakdown** — phased implementation steps with specific code changes
5. **Edge cases** — documented risks and mitigations
6. **Open questions** — unresolved decisions deferred to implementation
7. **Acceptance criteria** — testable conditions for success
8. **Affected files table** — files the plan says will change

## Phase 2 — Read every referenced file

For **each** file path mentioned in the plan:

1. Read the actual file from the codebase (use `read_file`)
2. If the file doesn't exist, note it as a finding (stale reference)
3. If the file exists, catalog its actual state relevant to the plan's claims

Also read files the plan **should** reference but doesn't:

- Files in the same directory as referenced files (e.g., other compose overlays, other Dockerfiles)
- Scripts or configs that interact with the components the plan modifies
- Test files that cover the code the plan changes

Use `list_files` on relevant directories to discover files the plan may have missed.

## Phase 3 — Cross-reference and verify

For each claim in the plan, verify against the actual codebase:

| Claim type | Verification |
|---|---|
| "File X has no `USER` directive" | Read file X, check for `USER` |
| "Script Y uses `$HOME`" | Read script Y, search for `$HOME` |
| "Config Z targets `/root/...`" | Read config Z, check paths |
| "No references to pattern P in directory D" | `search_files` for pattern P in D |
| "Service S binds port N" | Read compose/Caddyfile, check port |
| "Volume V is declared" | Read compose files, check volumes |

Record every discrepancy as a finding.

## Phase 4 — Classify findings

Rate each finding by severity:

| Severity | Definition | Examples |
|---|---|---|
| **Critical** | Would cause implementation failure or data loss if not fixed | Contradictory instructions in different sections; `chown` in a step that runs as non-root; cache mount target mismatch |
| **Significant** | Would cause confusion, wasted effort, or incomplete implementation | Missing files from affected-files table; stale references to removed code; gaps in test coverage |
| **Minor** | Improves clarity or completeness but won't block implementation | Effort estimate too optimistic; missing rollback plan; open questions that could be resolved now |

Also classify by category:

| Category | Description |
|---|---|
| **Contradiction** | Two sections of the plan say opposite things |
| **Gap** | Something the plan should address but doesn't |
| **Stale reference** | Plan describes code that has changed since the plan was written |
| **Technical bug** | Proposed code change would not work as described |
| **Missing edge case** | Risk not covered in the edge cases table |
| **Process** | Open questions, effort estimates, rollback plans |
| **Test gap** | Acceptance criteria or scenarios not covered by the test plan |

## Phase 5 — Produce the review document

Create a markdown review at `plan_docs/<plan-name>-review.md` (or alongside the plan). Structure:

```markdown
# Review of `<plan-document-name>`

## Overall Assessment

<1-2 sentence summary: is the plan well-structured? What's the main risk?>

---

### Critical Issues (Must Fix Before Implementation)

#### 1. <Issue title>

**<Section/Step reference>** says:
> <verbatim quote from the plan>

**<Other section/actual code>** says:
> <verbatim quote showing the contradiction or gap>

**Fix:** <specific, actionable fix>

---

### Significant Gaps

#### N. <Gap title>

<Description of what's missing and why it matters>

**Fix:** <what to add>

---

### Minor Issues / Improvements

#### N. <Issue title>

<Brief description>

---

### Summary Table

| # | Severity | Category | Description |
|---|----------|----------|-------------|
| 1 | **Critical** | Contradiction | <one-line description> |
| 2 | Significant | Gap | <one-line description> |
| ... | Minor | Process | <one-line description> |
```

Rules for the review document:

- **Quote verbatim** from both the plan and the actual code — never paraphrase when a quote is available
- **Be specific** — "Phase 1 Step 5" not "the config-copy step"
- **Every finding gets a Fix** — don't just identify problems, propose solutions
- **Summary table at the end** — gives a quick-scan overview
- **Count matters** — "4 critical, 6 significant, 6 minor" tells the user the scope

## Phase 6 — Present the review

1. Show the summary table and severity counts to the user
2. Provide a risk-calibrated reframing: separate spec-document fixes (low effort) from actual implementation risk
3. Ask: **"Want me to apply these fixes to the plan document?"**

Do **not** apply fixes until the user confirms.

## Phase 7 — Apply fixes (if approved)

If the user approves:

1. Apply all fixes to the plan document using `replace_in_file`
2. Work in batches of 4-5 SEARCH/REPLACE blocks to avoid tool errors
3. After each batch, verify the changes applied correctly
4. After all fixes, do a final consistency pass:
   - Check that all tables (Design Decisions, Affected Files, Edge Cases) agree with the task breakdown
   - Check that Open Questions are resolved (not just recommended)
   - Check that the effort estimate reflects the actual scope
5. Present the final state with `attempt_completion`

## Failure handling

| Situation | Action |
|---|---|
| Plan references a file that doesn't exist | Note as "Stale reference" finding; check if the file was renamed or removed |
| Plan claims "no references to X" but search finds them | Note as "Stale reference" with the actual matches |
| Two plan sections contradict each other | Note as "Critical — Contradiction"; propose the correct answer based on codebase evidence |
| Plan has unresolved Open Questions | Note as "Minor — Process"; propose resolutions based on codebase analysis |
| `replace_in_file` fails during fix application | Re-read the file, adjust SEARCH blocks to match current content, retry in smaller batches |
| User declines to apply fixes | Present the review document path and end |

## Quality checklist (self-review before presenting)

Before showing the review to the user, verify:

- [ ] Every file path in the plan was read and verified
- [ ] Every "no references to X" claim was checked with `search_files`
- [ ] All compose overlays (not just the base file) were checked
- [ ] All build-only Dockerfiles were considered (even if out of scope)
- [ ] The Affected Files table was cross-referenced against the task breakdown
- [ ] Edge Cases table was checked for internal contradictions with task steps
- [ ] Open Questions were evaluated for resolvability from existing code
- [ ] The effort estimate was sanity-checked against the number of changes
- [ ] Every finding has a specific, actionable Fix
- [ ] The summary table accurately reflects all findings

## Example invocation

```
User: review plan_docs/non-root-containers-spec.md
Agent: [reads plan, reads all referenced Dockerfiles/compose/scripts, cross-references]
Agent: [produces plan_docs/non-root-containers-spec-review.md with 16 findings]
Agent: "Found 4 critical, 6 significant, 6 minor issues. Want me to apply fixes?"
User: yes
Agent: [applies all 16 fixes in batches, verifies consistency]
Agent: "All fixes applied. Spec is now internally consistent and ready for implementation."