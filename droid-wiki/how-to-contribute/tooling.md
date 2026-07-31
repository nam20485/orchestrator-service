# Tooling

All operator/contributor scripts live in
`scripts/` and are PowerShell (pwsh) thin
wrappers, except the container entrypoints (bash). Dot-source auth helpers;
run the rest directly.

## Validation and setup

| Script | Role |
|--------|------|
| `scripts/validate.ps1` | Local validation gate mirroring CI: `-All` (default), `-Lint`, `-Scan`, `-Test`. See [Testing](testing.md) for what each subset covers. |
| `scripts/install-dev-tools.ps1` | One-time per-machine setup: `uv sync --group dev`, Pester 5+, and the Beads ecosystem (`br`, `bvr` via `cargo +nightly`, pinned commit revisions). Warns (does not fail) if `actionlint`, `shellcheck`, `jq`, or `docker` are missing, since those steps are individually skippable in `validate.ps1`. |

## Dispatch and attach (OpenCode client)

| Script | Role |
|--------|------|
| `scripts/prompt.ps1` | Dispatches a one-shot OpenCode run: `opencode run --attach <url> --dir <workspace> --model ... --agent ...`. Resolves the server URL as `-ServerUrl` > `$OPENCODE_SERVER_URL` > `$OPENCODE_HOST`/`$OPENCODE_PORT` > `http://localhost:4099`. Default model `zai-coding-plan/glm-5`, default agent `orchestrator`. Always resolves `-Workspace` to an isolated per-project subdir via `init-project-workspace.ps1` — the bare `/workspace` root is never used directly. Passes `--thinking`/`--auto`/`--print-logs` only when their corresponding param is `"true"` (they are boolean opencode flags that take no argument). This is the script the webhook receiver invokes for every dispatched run. |
| `scripts/attach.ps1` | Interactive `opencode attach <url>` session, same server-URL and workspace resolution as `prompt.ps1`. Accepts `-Password` as a `[SecureString]` (converted to plain text only at the point of use, to avoid leaking it via `$PSBoundParameters` or logs); falls back to `$OPENCODE_SERVER_PASSWORD`/`$OPENCODE_SERVER_USERNAME`. |
| `scripts/init-project-workspace.ps1` | Shared helper (dot-sourced by `prompt.ps1`/`attach.ps1`), not invoked directly. Creates `/workspace/<slug>/` and resolves `-Project` into the correct per-project subdir. |

## Compose

| Script | Role |
|--------|------|
| `scripts/dc.ps1` | Thin `docker compose` wrapper that exports `IMAGE_REF` (`main`/`development`/`nam20485`) for the tag interpolation compose files use. Accepts abbreviated commands (`u`=up, `d`=down, `l`=logs). `up` defaults to `-d` and always adds `--pull always` (unless `--build` is in the extra args, since `compose.build.yaml` sets `pull_policy: never`) so a stale local image cache never shadows the published `<branch>-latest` tag. Restores any prior `$IMAGE_REF` afterward so it doesn't leak into a later bare `docker compose` call. |

## GitHub / auth helpers

| Script | Role |
|--------|------|
| `scripts/common-auth.ps1` | `Initialize-GitHubAuth` with an interactive `gh auth login` fallback. Dot-source when you need `gh` auth interactively. |
| `scripts/gh-auth.ps1` | `Initialize-GitHubAuth` with PAT support (`-Token` / `$GITHUB_AUTH_TOKEN`), non-interactive. Dot-source for unattended (CI/agent) runs. |
| `scripts/test-github-permissions.ps1` | Verifies `gh` auth and scopes (repo, project) plus repo/milestone/branch/PR operations; `-AutoFixAuth` refreshes scopes. |
| `scripts/import-labels.ps1` | Syncs labels from a JSON export into a repo (create/update, `-DeleteMissing`). |
| `scripts/create-milestones.ps1` | Creates milestones from `-Titles` or `-TitlesFile`. |
| `scripts/link-github-tracker.ps1` | Backfill/repair: links GitHub issues to a Project V2 + milestone, and PRs to issues. |
| `scripts/query.ps1` | Lists/resolves unresolved PR review threads via GraphQL; `-DryRun`, `-AutoResolve`, `-ReplyEach "<msg>"`. Use this to close out review comments before merging (see [Development workflow](development-workflow.md)). |
| `scripts/sync-agent-instruction-indices.ps1` | Reconciles two local agent-instruction indices against the canonical `nam20485/agent-instructions` repo via the GitHub Contents API. |

## Container entrypoints (bash — not invoked manually)

| Script | Role |
|--------|------|
| `scripts/docker-entrypoint.sh` | `orchestratorservice` entrypoint. Writes `~/.local/share/opencode/auth.json` from whichever of `ZAI_CODING_API_KEY`/`ZAI_API_KEY`/`OPENROUTER_API_KEY`/`MODEL_STUDIO_API_KEY` is set, chowns the runtime data tree back to `app`, self-heals a corrupted `memory.jsonl` (backs it up and starts fresh if it fails to parse), then execs `opencode serve` as `app` via `gosu` (falling back to a direct exec with a warning if `gosu` is unavailable, e.g. when the entrypoint is tested on the host). |
| `scripts/webhook-entrypoint.sh` | `webhook-receiver` entrypoint. Runs as root before the `gosu` drop specifically to chown the `WEBHOOK_LOG_DIR` bind mount to `app`, since Docker creates a non-existent bind-mount source as root-owned on first attach — see [Testing](testing.md) Layer 4 for the regression this exists to prevent. |
| `scripts/git-trust.sh` | Marks bind-mounted repos safe for git (`git config --global --add safe.directory`) so root-running containers don't reject them. |

## Export / docs

| Script | Role |
|--------|------|
| `scripts/export-openapi.py` | Regenerates the committed `docs/openapi.json` from the `webhook_receiver` FastAPI app's OpenAPI schema, with deterministic config. Run after changing any FastAPI route/model. |

## Secret scanning

`.cursor/skills/scan-uncommitted-secrets/scripts/scan.sh` is invoked by
`scripts/validate.ps1 -Scan` against changed files. Run it directly, or via
the `/safe-commit` skill (`.cursor/skills/safe-commit`), before every commit —
see [Development workflow](development-workflow.md).
