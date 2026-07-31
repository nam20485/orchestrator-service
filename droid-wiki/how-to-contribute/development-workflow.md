# Development workflow

## Working-tree safety

This project runs its three services (`orchestratorservice`, `webhook-receiver`,
`webhook-proxy`) as non-root containers, and their bind mounts and volumes end
up owned by the host operator (UID 1000 by default), not `root`. Practically,
this means:

- You should never need `sudo` to delete or modify files under `WORKSPACE_DIR`
  during normal operation (`README.md`,
  "Non-root execution"). If you find root-owned files there, it is either a
  one-time pre-migration artifact (fixed with a single
  `sudo chown -R $(id -u):$(id -g) "$WORKSPACE_DIR"`) or a bug worth
  investigating, not routine.
- The `app`/`caddy` container users are baked in at **image build** time via
  `ARG APP_UID`/`APP_GID` (see
  `docs/deployment-compose.md`). If your host UID
  is not 1000, rebuild the images with matching build args instead of trying
  to work around ownership at runtime.
- Never commit real credentials. `scripts/validate.ps1 -Scan`
  runs `.cursor/skills/scan-uncommitted-secrets/scripts/scan.sh` against
  changed files and must pass cleanly before every commit. Test fixtures use
  only `FAKE-KEY-FOR-TESTING-…` placeholders
  (`test/fixtures/`) — never `ghp_`, `sk-`, or
  `AKIA` prefixes, even in throwaway examples.
- Provider credentials and secrets are host/CI environment variables, not a
  committed `.env`. `scripts/docker-entrypoint.sh` writes
  `auth.json` from env vars (`ZAI_CODING_API_KEY`, `OPENROUTER_API_KEY`,
  `MODEL_STUDIO_API_KEY`) at container start; do not add a project-level
  `.env` mechanism for these.

## Branching

Create a new branch per feature or fix, named `<base-branch-prefix>/<branch-name>`
(e.g. `mn/fix-watchdog-timeout`, `dev/add-dashboard-filter`) —
see `AGENTS.md`. The default remote branch is
`development` (`git symbolic-ref refs/remotes/origin/HEAD`); check
`git status --short --branch` before branching to confirm what you are
branching from.

## Making the change

- Keep changes surgical: only touch what the task requires
  (`AGENTS.md`, "Making Changes"). Ignore unrelated
  areas even if they look improvable.
- If you are adding new behavior, prefer TDD: write a failing test first
  under `tests/` (Python) or
  `test/` (Pester/bash), then implement until it
  passes. See [Testing](testing.md) for which layer to add a test to.
- Follow the structural and security conventions in
  [Patterns and conventions](patterns-and-conventions.md) when touching
  `webhook_receiver/`.

## Validating before commit

Run the full local gate, which mirrors CI (`.github/workflows/validate.yml`)
minus the Docker image build:

```bash
pwsh -NoProfile -File scripts/validate.ps1 -All
```

This runs lint, secret scan, and tests in sequence and stops at the first
failure (see [Testing](testing.md) for what each step covers). Missing local
tools (`actionlint`, `shellcheck`, Pester, the Beads `br`/`bvr` binaries)?
Run `scripts/install-dev-tools.ps1` once per machine —
see [Tooling](tooling.md).

Pre-commit checklist (`AGENTS.md`):

- `validate.ps1 -All` (or the relevant subset) passed.
- Secret scan clean.
- No real API keys or tokens in any committed file.
- Run the `/safe-commit` skill before committing (`.cursor/skills/safe-commit`).

## Pull requests and review

- Open a PR per branch with a descriptive title and description.
- Request review before merging.
- Address **every** review comment: reply explaining the resolution and mark
  the thread resolved. `scripts/query.ps1` can list and
  (with `-AutoResolve`/`-ReplyEach`) resolve unresolved review threads via the
  GitHub GraphQL API.

## After pushing: watch CI

CI is `.github/workflows/validate.yml`, with four jobs: `lint`, `scan`, `test`,
and `build` (Docker images; CI-only, not part of local `validate.ps1 -All`).
Monitor until green:

```bash
gh run list --limit 5
gh run watch <run-id>
gh run view <run-id> --log-failed
```

Never guess at a CI failure. Fetch the actual failing job's logs
(`gh run view <run-id> --log-failed`) and quote the exact failing line before
proposing a fix (`AGENTS.md`, "Diagnosing GHA
failures"). Do not consider work complete while CI is red.
