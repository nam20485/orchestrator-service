# Design Decisions

See [Background](index.md) for the overall system and [Pitfalls](pitfalls.md) for the incidents that motivated several of these choices.

## Non-root containers, built-into-the-image UIDs

All three containers (`orchestratorservice`, `webhook-receiver`, `webhook-proxy`) run as non-root at steady state: `app` (UID 1000 by default) via a `gosu` privilege drop, and Caddy via a `caddy` user created in the image (the pinned `caddy:2.10.0-alpine` ships none) dropped with `su-exec`. The UID/GID are baked in at **build** time (`ARG APP_UID`/`APP_GID` in `Dockerfile`), not set at runtime via compose `user:`.

The rationale, from `README.md` and `docs/deployment-compose.md`: a runtime `user:` override would bypass the root→`gosu`/`su-exec` drop and start the container as an arbitrary numeric UID that cannot write the 1000-owned `/home/app` or `/app/.memory`. Files written under `WORKSPACE_DIR` end up host-operator-owned, so cleanup needs no `sudo`. Changing the effective UID (non-1000 hosts) requires a **rebuild** with `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)`, not an env var — this is a deliberate trade-off of build-time coupling for correct file ownership.

## Beads DAG execution as the third pipeline tier

The `BeadsLoop` (`webhook_receiver/beads_loop.py`) is a background daemon thread in `webhook-receiver`. It scans `BEADS_WORKSPACE_ROOT` for project subdirectories containing `.beads/` (`webhook_receiver/workspace.py::discover_projects`), selects the next ready bead per project via `bvr --robot-next` (graph-aware) falling back to `br ready --json` + priority sort, and spawns an isolated agent in a per-bead git worktree (`.worktrees/<bead-id>/`, created by `create_bead_worktree` in `workspace.py`) to implement, test, and close it.

Per-bead worktrees are deliberately isolated and never shared — `workspace.py` documents that each worktree checks out the default branch HEAD, so `_plan_tracked()` only considers files present in a fresh checkout when deciding whether the project's plan doc is already committed. This means uncommitted-but-not-yet-committed plan files are invisible to a worktree, a constraint baked into how the loop verifies plan state before dispatching work.

"Ready at will" is a first-class design goal: `NOT_INITIALIZED` (no `.beads/` yet) and an empty `br ready`/`bvr` result are treated as normal idle states, not errors, so the service can start before any project exists and pick up work the moment `/plan-to-beads` creates a DAG — no restart required (`README.md`, `AGENTS.md`).

## Compose file layering instead of one monolithic file

Four compose files with distinct roles (`docs/deployment-compose.md`):

- `compose.yaml` — base/production, pulls `:main-latest` from GHCR.
- `compose.development.yaml` — **standalone**, re-declares every service for the dev stack; pulls `:development-latest`.
- `compose.https.yaml` — TLS overlay adding host `443:443` to Caddy, layered on `compose.yaml`.
- `compose.build.yaml` — local-build overlay (`build:` contexts, `pull_policy: never`), layered on either base file.

The `compose.development.yaml` duplication (rather than an overlay on `compose.yaml`) is an explicit trade-off documented as a "gotcha": a new environment variable must be added to **both** files, because dev is intentionally self-contained rather than composed from the prod base.

## Hybrid GitHub auth: App for delivery, PAT for orchestration

Per `AGENTS.md`, the GitHub App handles signed webhook delivery and event subscriptions only; the orchestrator's own `gh`/API calls run on `GH_ORCHESTRATION_AGENT_TOKEN` (a PAT), not an App installation JWT. The webhook receiver never mints installation tokens. This split keeps webhook signature verification (App-scoped, narrow) separate from the broader `gh` operations an agent performs (PAT-scoped, org-level), rather than trying to route all API access through one credential type.

## Activity-aware watchdog, not a single wall-clock timeout

`webhook_receiver/watchdog.py` replaces an earlier single `proc.wait(timeout=...)` design with three independent kill conditions: idle timeout (`IDLE_TIMEOUT_SECS`, default 900s, no stdout/stderr growth), consecutive errors (`MAX_CONSECUTIVE_ERRORS`, default 5), and a hard ceiling (`HARD_CEILING_SECS`, default 5400s) regardless of activity. The module docstring explains why: the previous system ran the opencode client and server in the same container and could inspect `/proc/<pid>/io`; this system runs them in separate Docker containers, so `/proc` is inaccessible, and the watchdog instead tracks stdout/stderr line freshness plus opencode server log growth (via `OPENCODE_SERVER_LOG_PATH`) as an activity signal — the latter specifically to avoid false-positive kills when a run delegates to a subagent and the client goes silent while the server keeps working (`docs/orchestrator-run-logs.md`).

## Single-writer invariant for the memory knowledge graph

`docs/tool-memory.md` documents that `@modelcontextprotocol/server-memory` writes the entire `memory.jsonl` via an unprotected `writeFile` on every mutation. Because each OpenCode session (orchestrator plus every delegated subagent) spawns its own server-memory process against the same `MEMORY_FILE_PATH`, concurrent writers interleave and corrupt the file. The fix is architectural, not a library patch: only the orchestrator calls write tools; subagents are read-only and return facts via a `## Memory Save Requests` list for the orchestrator to persist. `scripts/docker-entrypoint.sh` also self-heals a corrupted `memory.jsonl` at container start by quarantining it and creating a fresh file.

## Token-gated dashboard and simulator, disabled by default

`webhook_receiver/dashboard.py` and `webhook_receiver/simulator.py` are both gated by `DASHBOARD_TOKEN` (`webhook_receiver/auth.py`): every dashboard route 404s until the token is set, and the simulator (itself gated separately by `WEBHOOK_ENABLE_SIMULATOR`) 401s without a token even when enabled. This closes off the operational UI and the webhook-replay tool by default rather than requiring an explicit opt-out, consistent with the repo's no-committed-secrets stance (`README.md`, `docs/deployment-compose.md`).

## Beads CLI baked into the image, not compiled at runtime

`Dockerfile` builds `br` in a dedicated `rust-builder` stage (`cargo install --git ... --rev d9f8d7083dee46d04a8e4741c5f535eb7fcabc97 --locked beads_rust`) and copies the binary into the final image. In CI/local builds this stage compiles from source as a self-contained fallback; `.github/workflows/docker-publish.yml` overrides the `rust-builder` stage via `build-contexts` with a published GHCR beads image so the Rust toolchain isn't recompiled per image build. This directly addresses a prior failure mode where `br` was absent from the runtime image and an agent had to bootstrap a full Rust nightly toolchain mid-session (see [Pitfalls](pitfalls.md)).
