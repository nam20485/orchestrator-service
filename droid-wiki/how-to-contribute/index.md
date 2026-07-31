# How to contribute

This section is the practical companion to the architecture pages: how to get
a working environment, make a change safely, validate it the way CI does, and
diagnose problems when the running system misbehaves.

## Pages

- [Development workflow](development-workflow.md) — working-tree safety, branching, commit/PR expectations, and the CI gate.
- [Testing](testing.md) — the test layers (`test/`, `tests/`), what each one actually proves, and how to run them locally.
- [Debugging](debugging.md) — operational debugging: health checks, run logs, the dashboard, and the watchdog conditions that kill a run.
- [Tooling](tooling.md) — what each script in `scripts/` is for and when to reach for it.
- [Patterns and conventions](patterns-and-conventions.md) — code structure, security boundaries, and runtime/concurrency rules to preserve when changing `webhook_receiver/`.

## Before you start

Read `AGENTS.md` first — it is the authoritative
contributor contract for this repo (validation commands, branching rules, secret-scan requirements,
and the three-tier architecture this service implements). The pages in this section expand on it
with concrete commands and file references; they do not replace it.

## Fastest path to a validated change

1. Install missing local tools once: `pwsh -NoProfile -File scripts/install-dev-tools.ps1`.
2. Branch from the current base branch (see [Development workflow](development-workflow.md)).
3. Make the smallest change that fixes the issue.
4. Run `pwsh -NoProfile -File scripts/validate.ps1 -All` and fix everything it reports.
5. Commit, push, open a PR, and watch CI (`.github/workflows/validate.yml`) go green before considering the work done.
