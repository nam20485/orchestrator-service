# Testing

The suite has several distinct layers, each run by
`scripts/validate.ps1` and mirrored by
`.github/workflows/validate.yml`. Know which layer
you are adding to, and what it actually proves — one layer in particular
(`test/test-*.sh` container/config scripts) proves less than its name
suggests.

## Running everything

```bash
pwsh -NoProfile -File scripts/validate.ps1 -All   # lint + scan + test
pwsh -NoProfile -File scripts/validate.ps1 -Lint
pwsh -NoProfile -File scripts/validate.ps1 -Scan
pwsh -NoProfile -File scripts/validate.ps1 -Test
```

CI runs the same subsets as separate jobs (`lint`, `scan`, `test`) plus a
CI-only `build` job that actually builds the Docker images
(`.github/workflows/validate.yml`). Local
`validate.ps1` deliberately never builds images — image building is CI-only by
repo convention.

## Layer 1: Python unit tests (`tests/`)

Run directly with `uv run pytest tests/ -q` or via `validate.ps1 -Test`, which
additionally collects coverage:

```bash
uv run pytest tests/ -q --cov=webhook_receiver --cov-report=term-missing \
  --cov-report=html:htmlcov --cov-report=xml:coverage.xml
```

`tests/` covers pure Python logic in
`webhook_receiver/`: filters (`test_filters.py`), the runner
(`test_runner.py`), the Beads loop (`test_beads_loop.py`), the dashboard API
(`test_dashboard.py`), the watchdog (`test_watchdog.py`), workspace resolution
(`test_workspace.py`), config parsing (`test_config.py`), plus end-to-end and
integration variants (`test_e2e_beads_dispatch.py`,
`test_integration_beads.py`, `test_integration_webhook.py`,
`test_integration_dispatch.py`). These are real tests of behavior, not of
source text, and are the primary local signal for `webhook_receiver/` changes
(`docs/testing-approach.md`, section 4.1).
Coverage must stay above 85% as new code is added
(`AGENTS.md`).

## Layer 2: Pester tests (`test/*.Tests.ps1`)

Run via `pwsh -NoProfile -File test/run-pester-tests.ps1`
(also invoked by `validate.ps1 -Test`). These cover the PowerShell client
scripts: `PromptPs1.Tests.ps1`, `AttachPs1.Tests.ps1`, `DcPs1.Tests.ps1`,
`InitProjectWorkspace.Tests.ps1`, `LinkGithubTracker.Tests.ps1`,
`ValidatePs1.Tests.ps1`, `sync-agent-instruction-indices.Tests.ps1` — all
under `test/`.

## Layer 3: Bash integration/config scripts (`test/*.sh`)

Invoked individually by `validate.ps1` (both `-Lint` and `-Test`, with some
overlap): `test-compose-config.sh`, `test-caddyfile.sh`,
`test-opencode-json.sh`, `test-beads-versions-consistency.sh`,
`test-webhook-scripts.sh`, `test-memory-protocol.sh`,
`test-docker-entrypoint.sh`, `test-scan-secrets.sh`, `test-docker-user.sh`,
`test-docker-healthchecks.sh`, `test-openapi-schema.sh`.

**Read this layer's limits carefully.** Per
`docs/testing-approach.md`, most of these are
*source-grep assertions* over Dockerfile/compose text — they prove "the
recipe says X," not "the built image does X." A documented real regression
(`webhook-receiver` crashing on every dispatch with a `PermissionError`) shipped
to a stale published image while every one of these scripts, plus the full
CI `test` job, stayed green, because none of them build or run the actual
image. Treat a green `validate.ps1 -Test` as proof the *source* is internally
consistent, not that the container behaves correctly at runtime.

## Layer 4: Functional image tests (CI `build` job only)

`test-webhook-image-entrypoint.sh` is the one script that runs a real,
just-built container and asserts real runtime behavior (non-root write
succeeds under a freshly-created, root-owned bind mount; the host-side chown
propagated). It only runs in the CI `build` job
(`.github/workflows/validate.yml`, "Verify webhook non-root log-dir write
(functional)" step), never in local `validate.ps1`, because it requires a
built image and image building is CI-only. If you change entrypoint,
privilege-drop, or bind-mount ownership behavior, add or extend a functional
test in this job, not another grep script — see
`docs/testing-approach.md` sections 4.3–4.5 for the
negative-control discipline expected of any new functional test.

## Choosing where to add a test

- New pure-Python logic in `webhook_receiver/` → `tests/`.
- New PowerShell script or flag → `test/*.Tests.ps1` (Pester).
- A new config invariant you can check by inspecting Dockerfile/compose text
  → a `test/*.sh` grep script, but label it honestly as lint, not a runtime
  guarantee.
- A claim about what the *running container* actually does (ownership,
  privilege drop, entrypoint execution order) → a functional test in the CI
  `build` job, proven against a deliberately broken variant first.
