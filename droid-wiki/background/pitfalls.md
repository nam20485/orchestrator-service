# Pitfalls

See [Background](index.md) for context and [Design Decisions](design-decisions.md) for the architectural responses to several of these incidents.

## Green tests, broken container: the entrypoint drift case study

Documented in full in `docs/testing-approach.md`. Every dispatched webhook crashed in production with:

```
PermissionError: [Errno 13] Permission denied: '/tmp/orchestrator-webhook/prompt-XXXX.md'
```

raised at `webhook_receiver/runner.py:210` (`tempfile.mkstemp(... dir=log_dir)`), because `/tmp/orchestrator-webhook` is a host bind mount that Docker creates as `root:root` on first attach when the source host path doesn't exist yet, while the webhook process runs as non-root `app` (UID 1000) after the `gosu` drop.

The intended fix (commit `a77df2e`) added `scripts/webhook-entrypoint.sh`, which chowns the mount to `app` **before** the privilege drop, and wired it as `Dockerfile.webhook`'s real `ENTRYPOINT`. The deployed image was **stale** — built before that commit — so its entrypoint was still the bare `["gosu","app"]` form with no chown. The entire test suite stayed green throughout, because the container-behavior tests (`test/test-docker-user.sh`, `test/test-webhook-scripts.sh`, `test/test-compose-config.sh`) are source-grep assertions over Dockerfile/compose *text*, and `docker compose config` validates against the working tree, not the registry image actually running. None of them build or run the image.

The corrective direction adopted: keep pytest as the primary local signal, relabel the grep scripts as static lint (not runtime proof), and add a functional image-test layer (`test/test-webhook-image-entrypoint.sh`, wired into the `build` job of `.github/workflows/validate.yml`) that builds the real image, mounts a **non-existent** host path so Docker creates the root-owned dir exactly as in production, and asserts the dropped-to-`app` process can actually write there and that the chown propagated to the host side. This layer intentionally lives in CI's `build` job only — `scripts/validate.ps1` never builds images, by repo convention.

## Idle-timeout / activity detection across delegated subagent sessions is hard

`plan_docs/.archived/idle-timeout-prev-version-warning.md` (historical note) flags that determining "activity" for delegated-subagent sessions is difficult to get reliably right — the prior system invested significant effort here before this repo's watchdog was designed. The resulting design (`webhook_receiver/watchdog.py`, described in [Design Decisions](design-decisions.md)) tracks opencode server log growth as a secondary activity signal specifically so a run that delegates to a subagent and goes silent on stdout isn't killed while the server is still working. `docs/orchestrator-run-logs.md`'s worked example (`gap-miner-v2-delta48`) shows the related failure mode from the other direction: the orchestrator delegated the entire workflow to a single subagent and never resumed, leaving the dispatch issue open with a `.stderr` stream that ends mid-workflow — a run that finishes with exit 0 but should be classified `incomplete`, not `completed`.

## No `bash` tool on the orchestrator agent leads to tool abuse

`plan_docs/.archived/beads-skill-execution-postmortem.md` records a real trace where the orchestrator agent, lacking a `bash` tool (`bash: false`, `permission: bash: deny`), attempted to run shell commands via `playwright_browser_run_code_unsafe` (`execSync` inside a Playwright `run_code_unsafe` call) before giving up and delegating to a specialist agent that does have `bash`. Any skill instruction that assumes the top-level orchestrator can run shell commands directly will trigger this same workaround pattern; skills must delegate shell-requiring steps to a subagent instead.

## Beads ID format mismatch broke `plan-to-beads` end-to-end

The same postmortem documents that the `plan-to-beads` skill hardcoded the bead ID format as `br-<hex>`, but actual `br` IDs are `<prefix>-<hex>` where the prefix is derived from the working directory's basename (e.g. `workspace-ryg`). Every captured ID under the wrong assumption was empty, so `br dep add` received empty arguments and exited 1. This is a reminder that bead ID prefixes are workspace-basename-derived and must not be assumed constant across projects.

## `br` compiled from source at runtime before it was baked into the image

The same postmortem also found `br` absent from the orchestratorservice image at the time, forcing a full Rust toolchain bootstrap (rustup → nightly → `build-essential` → compiling 542 crates) inside a live agent session — extremely slow and fragile mid-dispatch. This is now resolved architecturally: `Dockerfile` builds `br` in a `rust-builder` stage and copies the binary into the final image (see [Design Decisions](design-decisions.md)), so the compile happens at image build time, not agent runtime. The incident is retained here because the failure mode — an agent trying to bootstrap a missing toolchain mid-session — recurs any time a new CLI dependency is added to a skill without also adding it to the image.

## `br` transient JSONL flush warnings are not failures

`br` v0.2.15 emits a transient auto-flush warning ("expected canonicalized numbered placeholder") on some mutations. The postmortem confirms the underlying mutation still succeeds and `br sync --flush-only` reconciles the JSONL (`br sync --status` reports "In sync" afterward). Treating this warning as a hard failure in a skill or agent would cause unnecessary retries/aborts on otherwise-successful bead operations.

## Compose env-var duplication drift between prod and dev

Because `compose.development.yaml` is standalone rather than an overlay on `compose.yaml` (see [Design Decisions](design-decisions.md)), `docs/deployment-compose.md` calls out explicitly that adding a new environment variable (its own example: `DASHBOARD_TOKEN`) to only one of the two files silently breaks the other environment. There is no automated check for this drift; it is a manual-discipline requirement each time a variable is added.
