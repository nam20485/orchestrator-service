Single PR on branch `dev/agent-readiness-quick-wins` (base `development`) implementing the 13 selected quick wins from `docs/agent-readiness-action-plan.md`. Ordered as commits so the large formatting diff lands first and stays reviewable.

## Commit 1 — `style: apply ruff format` (item 8, prep)
`ruff format --check` currently reports **32 of 41 files would be reformatted**. Run `uv run ruff format .` as its own commit (pure whitespace/wrapping churn, no logic) so later commits stay readable.

## Commit 2 — Lint/test gates (items 2, 8, 9, 10)
**Item 8 — `formatter`**: enforce ruff's formatter so agents stop producing style-only diffs.
- `scripts/validate.ps1` `-Lint`: add `uv run ruff format --check .` after the existing `ruff check`.
- `.github/workflows/validate.yml` needs no change (the `lint` job calls `validate.ps1 -Lint`).

**Item 9 — `cyclomatic_complexity`**: fail lint on newly-added deeply-branching functions.
- `pyproject.toml`: add `C901` to `[tool.ruff.lint] select`, `[tool.ruff.lint.mccabe] max-complexity = 15`.
- Current baseline is 7 violations at threshold 10: `simulator.create_simulator_router` (15), `run_narrative._summarize_event` (14), `watchdog.run` (14), `watchdog.poll` (11), `run_stream.parse_events` (11) all pass at 15; the two outliers get scoped ignores with the measured value in the comment:
  `[tool.ruff.lint.per-file-ignores]` → `"webhook_receiver/dashboard.py" = ["C901"]` (`create_dashboard_router`, 37 — router factory with ~15 nested handlers) and `"webhook_receiver/runner.py" = ["C901"]` (`_run_completion_watcher`, 22). Both are ratchet exemptions, recorded as a deferred refactor in the action-plan doc, not silenced silently.

**Item 10 — `test_isolation`**: prove tests don't depend on execution order/shared state, and cut suite time.
- Add `pytest-xdist` to `[dependency-groups] dev`; `uv lock`.
- `scripts/validate.ps1` `-Test`: add `-n auto` to the pytest invocation (CI inherits it). Left out of `pyproject` `addopts` so single-file debugging stays serial.
- Expect flakes from module-level state (`dashboard._CACHE`, watchdog/beads_loop globals, shared tmp paths). Fix by converting the offending globals to fixtures with `monkeypatch`/autouse reset rather than pinning tests to one worker; if any test is genuinely process-global, mark it `@pytest.mark.xdist_group` instead of disabling parallelism.

**Item 2 — `test_coverage_thresholds`**: make AGENTS.md's ">85%" rule executable (currently 91.26%, ~6 pts headroom).
- `scripts/validate.ps1` `-Test`: add `--cov-fail-under=85`.
- `test/ValidatePs1.Tests.ps1`: assert `validate.ps1` contains `ruff format --check`, `-n auto`, and `--cov-fail-under=85` so the gates can't be silently dropped.

## Commit 3 — Repo hygiene + automation config (items 1, 4, 6, 14)
**Item 1 — `dependency_update_automation`**: no Dependabot/Renovate exists today.
- New `.github/dependabot.yml`, weekly, with `open-pull-requests-limit` and grouping: `uv` (root, tracked `pyproject.toml` + `uv.lock`), `github-actions` (`/`), and `docker` via `directories: ["/", "/deploy/caddy"]` (covers `Dockerfile`, `Dockerfile.webhook`, `Dockerfile.beads`, `deploy/caddy/Dockerfile`). No npm ecosystem — `.opencode/package.json` is gitignored, so nothing npm is tracked.

**Item 4 — `pre_commit_hooks`**: shift lint/secret-scan left of CI.
- New `.pre-commit-config.yaml`: `ruff-pre-commit` (`ruff check --fix`, `ruff format`) pinned by rev, plus a `local` hook running the existing `.cursor/skills/scan-uncommitted-secrets` scanner, plus `check-added-large-files` / `check-merge-conflict`.
- Add `pre-commit` to the dev group; `scripts/install-dev-tools.ps1` runs `uv run pre-commit install` when the hook isn't present; document in AGENTS.md's pre-commit checklist.

**Item 6 — `gitignore_comprehensive`**: add `.DS_Store`, `Thumbs.db`, `desktop.ini`, root `node_modules/`, and `.vscode/*` with `!.vscode/settings.json` (settings.json is tracked and shared — keep it, ignore per-user files).

**Item 14 — `release_notes_automation`** (chosen: GitHub auto-generated notes on tag):
- New `.github/release.yml` categorizing notes by label (features / fixes / docs / dependencies, with `dependencies` excluded from "Other").
- New `.github/workflows/release.yml`: on `push` tag `v*.*.*`, `permissions: contents: write`, single step `gh release create "$GITHUB_REF_NAME" --generate-notes --verify-tag`. Complements the existing `docker-publish.yml` tag trigger; no version-bump bot, no commit-message discipline required.

## Commit 4 — Rollback path (item 3)
**Item 3 — `rollback_automation`**: `docker-publish.yml` already publishes immutable `<branch>-<run_number>` tags, but `compose.yaml` pins `:${IMAGE_REF:-main}-latest`, so there is currently **no way to run an older build** (`main-123` is a tag; `main-123-latest` is not).
- New `compose.rollback.yaml` overlay setting each service image to `...:${IMAGE_TAG}` (required var), following the existing overlay convention (`compose.https.yaml`, `compose.build.yaml`).
- New `scripts/rollback.ps1`: lists recent published tags (`gh api /users/.../packages/container/.../versions`), then `docker compose -f compose.yaml -f compose.rollback.yaml up -d` with `IMAGE_TAG` set; `-DryRun` prints the resolved command.
- New `docs/rollback-runbook.md`: how to identify the last-good run number, roll back, verify (`/health`, `docker compose ps`), and roll forward.
- `docker-publish.yml`: also emit `type=raw,value=sha-${{ github.sha }}` for the two runtime images so rollback can pin a commit, not just a run number.
- `test/test-compose-config.sh`: assert the overlay renders (`docker compose -f compose.yaml -f compose.rollback.yaml config` with `IMAGE_TAG` set) and that images resolve to the pinned tag.

## Commit 5 — Container health probes (item 13)
**Item 13 — `health_checks`**: `/health` exists in FastAPI but no image declares a Docker `HEALTHCHECK`, so orchestrators can't tell "started" from "serving".
- `Dockerfile.webhook`: `HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS "http://127.0.0.1:${WEBHOOK_PORT:-8080}/health" || exit 1` (curl is installed).
- `Dockerfile` (orchestratorservice): `opencode serve` is password-protected, so probe the listener, not a route: `CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/4099' || exit 1` (`--start-period=40s`).
- `deploy/caddy/Dockerfile`: alpine has no curl; use the Caddy admin API (enabled — the Caddyfile has no `admin off`): `CMD wget -q -O- http://127.0.0.1:2019/config/ >/dev/null 2>&1 || exit 1`.
- `compose.yaml`: upgrade `webhook-receiver`'s `depends_on: orchestratorservice` to `condition: service_healthy` so dispatch can't fire at a not-yet-listening server.
- New `test/test-docker-healthchecks.sh` (static contract check in the style of `test/test-docker-user.sh`): each of the three Dockerfiles declares `HEALTHCHECK` with the expected probe and a `--start-period`. Wire into `validate.ps1 -Test`.

## Commit 6 — Error tracking (item 12)
**Item 12 — `error_tracking_contextualized`**: dispatch failures land only in local logs today.
- `pyproject.toml`: `sentry-sdk[fastapi]` as a **required** runtime dependency (inert without a DSN); `uv lock`.
- `webhook_receiver/config.py`: add `sentry_dsn`, `sentry_environment`, `sentry_traces_sample_rate` (default `0.0`), `sentry_release` to `Settings` + `from_env()`.
- New `webhook_receiver/observability.py`: `init_sentry(settings)` (no-op when DSN is empty; sets `send_default_pii=False`) and no-op-safe context helpers.
- Call `init_sentry()` from `webhook_receiver/__main__.py` (production entry, already coverage-omitted) so tests/`create_app()` stay untouched.
- Contextualize: tag delivery id / event / repo / bead id at dispatch in `runner.py`, and capture the watchdog-kill and beads retry-exhausted paths — reusing the existing `_sanitize_for_comment()` so secrets never reach Sentry.
- `compose.yaml` env passthrough for the new vars; document them in the README/env table.
- New `tests/test_observability.py` (TDD): no DSN → `sentry_sdk.init` not called; DSN set → called with expected env/release/sample rate; context helpers no-op when uninitialized. Extend `tests/test_config.py` for the new settings.

## Commit 7 — API schema artifact (item 11)
**Item 11 — `api_schema_docs`**: no machine-readable contract is committed.
- New `scripts/export-openapi.py`: builds the app under a **pinned deterministic config** (placeholder `OS_WEBHOOK_SECRET`, simulator enabled, dashboard token set — the disabled variants register different 404-stub routes, so the config must be fixed for a stable diff) and writes sorted, newline-terminated JSON to `docs/openapi.json`. `--check` mode exits non-zero on drift.
- Commit `docs/openapi.json`.
- New `test/test-openapi-schema.sh` calling `scripts/export-openapi.py --check`; wire into `validate.ps1 -Test` so a route change without a regen fails locally and in CI.

## Commit 8 — DevContainer + docs (items 15, and action-plan bookkeeping)
**Item 15 — `devcontainer`**: one-command reproducible env for agents/new machines.
- New `.devcontainer/devcontainer.json`: `mcr.microsoft.com/devcontainers/python:1-3.11-bookworm` + features `node` (24), `powershell`, `github-cli`, `docker-in-docker`, `uv`; `postCreateCommand` = `uv sync --group dev` + Pester install + `pre-commit install`; `forwardPorts` `[80, 4099, 8080]`; no secrets baked (documented as host env passthrough).
- New `test/RepoConfig.Tests.ps1` (Pester): `.devcontainer/devcontainer.json` is valid JSON with the expected features; `.github/dependabot.yml` covers uv/github-actions/docker; `.pre-commit-config.yaml` references ruff + the secret scanner; `.github/workflows/release.yml` + `.github/release.yml` exist; `.gitignore` contains the new entries.
- Docs: update AGENTS.md (Validation/Testing/pre-commit sections for the new gates, devcontainer, rollback) and mark items 1, 2, 3, 4, 6, 8, 9, 10, 11, 12, 13, 14, 15 done in `docs/agent-readiness-action-plan.md`, recording the two deferred C901 refactors.

## Validation
1. `pwsh -NoProfile -File ./scripts/validate.ps1 -All` after each commit; fix until clean (expect real work in the `-n auto` flake fixes).
2. `uv run pytest tests/ -q -n auto` a second time to confirm parallel stability isn't order-luck.
3. `bash test/test-docker-healthchecks.sh`, `bash test/test-compose-config.sh`, `bash test/test-openapi-schema.sh`, `pwsh -File ./test/run-pester-tests.ps1` individually.
4. `actionlint` on the new `release.yml` (covered by `validate.ps1 -Lint`).
5. Secret scan clean (`validate.ps1 -Scan`) — no DSNs or tokens in committed files.
6. Push, open the PR, then `gh run watch` until `validate` (lint/scan/test/**build** — the build job is the only place the new `HEALTHCHECK` lines actually get exercised) is green. Docker `HEALTHCHECK` runtime behavior verified locally with `docker compose up -d && docker inspect --format '{{.State.Health.Status}}'` per service.
