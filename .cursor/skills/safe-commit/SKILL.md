---
name: safe-commit
description: >-
  Runs scan-uncommitted-secrets, then groups outstanding changes into logical
  commits with meaningful messages. Use when the user asks for safe-commit,
  safe commit, commit changes safely, or grouped commits after a secret scan.
disable-model-invocation: true
---

# Safe Commit

Commit outstanding work only after a clean secret scan, split into focused commits by concept.

## Prerequisites

- User invoked **safe-commit** (this skill is the commit authorization)
- Worktree has uncommitted changes worth committing
- Do **not** commit credential files or hardcoded secrets

## Phase 1 — Secret scan (mandatory)

Follow [scan-uncommitted-secrets](../scan-uncommitted-secrets/SKILL.md):

```bash
bash .cursor/skills/scan-uncommitted-secrets/scripts/scan.sh
```

**If exit code is non-zero:** stop immediately. Do not stage or commit. Report findings using the scan skill's response format and help remediate.

**If exit code is 0:** proceed to Phase 2.

Also verify secrets stay ignored:

```bash
git add --dry-run -A
```

Abort if dry-run would add credential files.

## Phase 2 — Understand changes

Run in **parallel**:

```bash
git status
git diff
git diff --cached
git log -10 --oneline
```

Read enough of each changed file to assign it to a group. Exclude:

- `.cursor/hooks/state/` machine-local state (usually skip committing)
- Files the user explicitly asked not to commit
- Obvious noise unless the user wants everything committed
- any files already in `.gitignore`

## Phase 3 — Group changes conceptually

Partition files into **1–6 logical groups**. Each group should be one coherent story a reviewer can understand in one glance.

### Grouping heuristics

| Group theme | Typical paths |
|-------------|---------------|
| Container runtime | `Dockerfile`, `compose.yaml`, `.dockerignore`, `scripts/docker-entrypoint.sh` |
| OpenCode server config | `image/opencode.json`, `image/AGENTS.md`, `image/.opencode/` |
| Client scripts | `scripts/prompt.ps1`, `scripts/attach.ps1`, `scripts/common-auth.ps1`, other `scripts/*.ps1` |
| Agent / Cursor tooling | `.cursor/skills/`, `.cursor/rules/` (not hook state) |
| Docs / memory | `AGENTS.md`, `plan_docs/`, `README.md` |
| Repo hygiene | `.gitattributes`, `.gitignore`, workspace files |

Rules:

- One file belongs to **one** group (pick the primary purpose)
- Prefer **more, smaller** commits over one large commit when themes differ
- Keep test-only or generated artifacts out unless intentional
- If only one theme exists, a single commit is fine

### Present the plan before committing

Show a short table:

```markdown
| # | Commit message (draft) | Files |
|---|------------------------|-------|
| 1 | ... | file_a, file_b |
| 2 | ... | file_c |
```

Then execute unless the user already constrained grouping in the same request.

## Phase 4 — Commit each group

For **each** group, in dependency-friendly order (infra → config → scripts → docs → tooling):

1. Stage only that group's files (`git add -- <paths>`)
2. Confirm staging: `git diff --cached --stat`
3. Commit with a HEREDOC message

### Commit message style

Match recent repo history: lowercase imperative/descriptive, 1–2 sentences focused on **why**.

```bash
git commit -m "$(cat <<'EOF'
add docker runtime and provider auth synthesis at container start

Generate opencode auth.json from host/CI environment variables before serve.
EOF
)"
```

Guidelines:

- First line ≤ 72 chars when possible; optional body after blank line
- No secrets, tokens, or PII in messages
- Use `add`, `update`, `fix`, `remove` accurately
- Do not bundle unrelated themes in one message

After each commit: `git status --short`

## Phase 5 — Final report

```markdown
## Safe commit complete

**Scan:** passed (no sensitive content)

**Commits created:**
1. `<hash>` — `<subject>`
2. `<hash>` — `<subject>`

**Skipped (if any):** `<paths>` — `<reason>`

**Remaining uncommitted:** none | `<list>`
```

## Git safety (required)

- NEVER update git config
- NEVER use `--no-verify`, `--no-gpg-sign`, or force push
- NEVER commit hardcoded API keys, passwords, or provider credential files
- NEVER amend unless user rules for amend are fully satisfied
- Do NOT push unless the user explicitly asks

## Failure handling

| Situation | Action |
|-----------|--------|
| Scan finds secrets | Stop; no commits |
| Empty group after exclusions | Skip group |
| Pre-commit hook fails | Fix issue; new commit (do not amend unless allowed) |
| Unclear grouping | Prefer smaller groups; note assumption in report |
| No changes left | Report "nothing to commit" |

## Example grouping (orchestrator-service)

```
1. fix dockerfile node install and multi-stage runtime layout
   → Dockerfile, .dockerignore, scripts/docker-entrypoint.sh, compose.yaml

2. configure opencode image defaults and memory-graph mcp
   → image/opencode.json, image/AGENTS.md, image/.opencode/

3. thin client scripts using host opencode password env
   → scripts/prompt.ps1, scripts/attach.ps1, scripts/common-auth.ps1

4. add scan-uncommitted-secrets and safe-commit agent skills
   → .cursor/skills/scan-uncommitted-secrets/, .cursor/skills/safe-commit/

5. record workspace conventions in agents memory
   → AGENTS.md
```

Adjust groups to match the actual diff — the example is illustrative, not fixed.
