---
name: scan-uncommitted-secrets
description: >-
  Scans staged, unstaged, and untracked git files for secrets, API keys,
  passwords, tokens, and PII before commit. Use when the user asks to check
  for sensitive content, pre-commit secret scan, leak prevention, or whether
  changed files are safe to commit.
disable-model-invocation: true
---

# Scan Uncommitted Secrets

Check all changed, uncommitted work (staged, unstaged, untracked, non-gitignored) for content that must not be committed.

## When to run

- Before `git commit` or `git add` when the user asks for a secret/safety check
- When the user asks to scan changed files for sensitive content
- After editing scripts, compose files, auth config, or env-related files

## Workflow

1. Run the scanner from the repo root:

```bash
bash .cursor/skills/scan-uncommitted-secrets/scripts/scan.sh
```

2. If exit code is **non-zero**, treat as a **hard stop**:
   - Warn loudly that sensitive content was found
   - Do **not** commit until resolved
   - Report every finding with **category**, **file:line**, **rule**, and the **matching line**

3. If exit code is **0**, report briefly that no sensitive content was detected in changed/untracked files.

4. Optionally cross-check with git:

```bash
git status --short
git add --dry-run -A
```

Confirm dry-run would not add credential files or `.env`.

## What the scanner checks

| Category | Examples |
|----------|----------|
| Blocked filenames | `.env*`, `auth.json`, `secrets.toml`, `credentials.json`, `*.pem`, `*.p12`, `id_rsa`, … |
| API keys | `AKIA…`, `sk-or-v1-…`, `sk-proj-…`, `sk-…` |
| Tokens | `ghp_…`, `ghs_…`, `gho_…`, `ghu_…`, `ghr_…`, `github_pat_…`, `glpat-…`, `xox…`, `Bearer …`, JWT (`eyJ…`) |
| Passwords | `--password "…"`, `password = "…"` |
| Secrets | Private key blocks, generic `api_key = "…"`, auth.json `"key": "…"` entries |
| Repo env vars | Hardcoded `OS_WEBHOOK_SECRET`, `OPENCODE_SERVER_PASSWORD`, `ZAI_CODING_API_KEY`, `ZAI_API_KEY`, `OPENROUTER_API_KEY`, `MODEL_STUDIO_API_KEY`, `GH_ORCHESTRATION_AGENT_TOKEN`, `GITHUB_TOKEN` (8+ char values) |
| PII | SSN patterns, email addresses, phone numbers (skipped in `*.lock` files) |

Allowlisted placeholders (for example `FAKE-*-FOR-TESTING`, `example.com`, `'…'`, `${VAR}` compose interpolation) are skipped.

Regression tests: `bash test/test-scan-secrets.sh` (also run via `validate.ps1 -Test`).

## Response format when findings exist

Use this structure and be explicit:

```markdown
## DO NOT COMMIT — sensitive content detected

Found **N** issue(s) in changed/untracked files:

### [CATEGORY] path/to/file.ext:LINE
- **Rule:** short rule name
- **Line:** `matching line content`

### Required actions
- Remove, redact, or replace secrets with environment variables / CI secrets
- Re-run the scanner until it exits 0
- Never commit API keys, passwords, tokens, or PII
```

## Repo-specific reminders

- Provider credentials belong in host or CI environment variables (`ZAI_CODING_API_KEY`, `MODEL_STUDIO_API_KEY`, `OS_WEBHOOK_SECRET`, etc.), not committed files
- Client scripts should use `$env:OPENCODE_SERVER_PASSWORD`, not hardcoded passwords
- Compose should use `${VAR}` interpolation, not literal secret values
- Scan results are advisory; review false positives (documentation mentioning `password` without values is usually fine)

## Blocked credential filenames

The scanner fails immediately (before line scan) if a changed file path matches:

`.env`, `.env.*`, `.envrc`, `auth.json`, `secrets.toml`, `.pypirc`, `credentials.json`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `*.pem`, `*.p12`

These must not be committed even when gitignored locally.

## Do not

- Commit files flagged by the scanner without user acknowledgment and remediation
- Print full secret values in chat if avoidable; cite file and line and describe the match type
- Skip scanning because "it looks fine" — run the script
