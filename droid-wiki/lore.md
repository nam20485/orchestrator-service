# Lore

A narrative history of `orchestrator-service`, reconstructed from git history (356 commits on this branch as of writing), tag names, and surviving source. Dates are commit dates (`%ad`, UTC as recorded by git) rather than confirmed calendar reality, since this environment's clock cannot be independently verified against the outside world; they are used here only to order events relative to each other.

See also: [overview/index.md](overview/index.md) and [overview/architecture.md](overview/architecture.md) for the current-state description this history led to.

For technical rationale and present-day operational hazards, see [Background](background/index.md).

## Era 1 — Bootstrapping a Python service (2026-05-27 to 2026-06-01)

The repository begins with `97f65c1 Initial commit`, followed immediately by workspace/config scaffolding (`ba5e614 initialize orchestrator-service with basic structure and configuration files`, `cd83b66 add workspace configuration file`). The earliest surviving intent is a conventional Python service: commit `56c02a3 Remove main.py as it is no longer needed for the orchestrator service, streamlining the codebase` shows a `main.py` entry point existed and was deliberately dropped within the first days, suggesting an early direction (a standalone Python app) was tried and abandoned in favor of something else — the commit message doesn't say what that "something else" was, so the immediate replacement plan is not confirmed by the log alone.

## Era 2 — The OpenCode server and agent roster (2026-06-01)

On the same day as the `main.py` removal, the shape of the current architecture appears in a burst of commits: `237867b add Docker configuration, entrypoint scripts, and agent documentation for OpenCode server setup`, `7ec0339 add new agent definitions for various roles including backend developer, cloud infra expert, and orchestrator...`, and `a7023c3 bundle opencode image config, github templates, and local instruction modules`. This establishes the long-lived `image/.opencode/` config tree and the container-based OpenCode server (`opencode serve`) that AGENTS.md still documents as the runtime's core. `9c9854c add container runtime with env auth, workspace volume, and dockerignore allowlist` and `f9022ab expand client scripts for prompt, auth, and github helpers` lay down `scripts/prompt.ps1` and the env-var-driven auth model that persists to the present.

## Era 3 — Webhook receiver and CI hardening (2026-06-01)

Still within the first active day of commits, `fe70456 add github webhook receiver that dispatches opencode runs` and `8885d1f add webhook docker image, caddy proxy, and compose stack` introduce the second long-lived service: the FastAPI `webhook_receiver` behind a Caddy proxy, dispatching OpenCode runs via `scripts/prompt.ps1`. This is followed by a wave of CI/security commits the same day — `a04ffe9 Add Trivy vulnerability scanner workflow`, `a46e426 Add Dependency Review Action workflow`, and `fabe75c add local validation suite, tests, and CI validate workflow` — establishing the lint/scan/test discipline (`scripts/validate.ps1`) that AGENTS.md still enforces as mandatory before commits.

## Era 4 — The Beads execution loop (2026-06-20 to 2026-06-30)

`c18b32e add beads-backed agent execution loop with workspace management` (2026-06-20) marks the start of the largest architectural addition after the initial bootstrap: the Beads DAG-driven `BeadsLoop` background thread. This era is dense with fixes pinning the Beads toolchain to specific releases (`ad4cba4 fix: pin beads_rust to released v0.2.15 tag (default-branch HEAD is broken)`, `a8cf2aa pin beads to immutable SHAs and add canonical builder image`), which — together with commits like `70416ee fix: bump rust-builder to 1.95 for beads_rust 0.2.15 MSRV` and `98228e0 fix: build beads_rust with nightly toolchain (#![feature] requirement)` — indicates the Beads Rust CLI (`br`/`bvr`) was an unstable, actively-evolving upstream dependency that required repeated version-pinning workarounds. `52d983f eliminate beads_target_repo config, restructure workspace to multi-project worktree model` (2026-06-28) and `9f0a832 refactor beads loop to scan multiple projects with project-scoped state` show an early single-project design being generalized to the current multi-project worktree model within about a week of the loop's introduction — a reorganization, not just a feature add.

## Era 5 — Hardening: non-root containers and dashboard (2026-06-30 to 2026-07-03)

`54cf92c docs: add specification for non-root container execution` and `14f0e16 Support non-root user execution (#30)` (2026-06-30) introduce the gosu/su-exec privilege-drop model AGENTS.md documents today, followed by fix-up commits (`25f8c17 fix: repair non-root container launch (caddy user, entrypoint perms)`, `bde3a27 test: implement docker-user contract assertions for non-root execution`) showing the change needed multiple correction passes before it stabilized. In parallel, `96c1f29 Enhance dashboard with beads graph, detail pages, and multi-project support (#40)` (2026-07-03) — preceded by earlier dashboard commits `9f87a15`/`acb6513 add beads graph + bvr pages views to dashboard` — adds the web dashboard as a third user-facing surface alongside the OpenCode server and webhook receiver, and `7064a98 harden dashboard against XSS, path injection, SSE backpressure, and track halted beads` shows an immediate security-hardening follow-up.

## Era 6 — Planning skills and the three-tier pipeline (2026-06-25 to 2026-07-24)

`daf72cb document three-tier architecture and beads normal states` and the addition of `9712fad add perfect-idea and plan-to-beads skills with task completion contract` formalize the Ideation → Planning → Execution pipeline that AGENTS.md now describes (`/perfect-idea` → `application_plan.md` → `/plan-to-beads` → `.beads/` DAG → `BeadsLoop`). Backup/checkpoint tags visible in the repo (`pre-discovery-path-alignment-20260703`, `pre-checkkry-picks`, `pre-security-capability-changes`, `nam20485-backup-prealign-20260703`) cluster around this period and the non-root/dashboard work, suggesting the team took manual safety snapshots before larger structural changes — the tags carry no annotated messages, so their exact intent beyond "pre-change backup" is inferred from naming alone, not confirmed.

## Era 7 — The permission-deadlock fix and dispatch refactors (2026-07-24 to present)

A cluster of commits in late July documents a specific incident-driven fix: `659dab8 add watchdog permission-ask deadlock detection`, `0748e86 rewrite headless permission-deadlock rule and add golf38 postmortem`, `791cab5 refactor: simplify permission config to use --auto flag`, and `e850ff0 align docs and comments to new permission: allow model` replace an earlier (silently non-functional) `--dangerously-skip-permissions` flag with the current two-layer `"permission": "allow"` + `--auto` model described in AGENTS.md. Most recently, `99c138a feat: add gh-issue-tracking label prefix for dispatch` and `12670e7 refactor(dispatch): use gh-issue-tracking:epic-implemented label` (2026-07-30) show the label-driven dispatch scheme still being actively renamed and refined, and `b1b1548 address PR #49 review: position-based ask/reply ordering, narrative maps, dev compose sync` / `664c477 docs: add plan to resolve PR #49 review comments` (2026-07-31) are the most recent commits on this branch at analysis time.

## Longest-standing components

Cross-referencing "added early" against "still present in the working tree" shows a few pieces have survived essentially unchanged in role since Era 2–3, on 2026-06-01:

- **`image/.opencode/` config tree** (`opencode.json`, `AGENTS.md`, `agents/`, `commands/`, `skills/`) — introduced in `a7023c3`, still the authoritative OpenCode server config per AGENTS.md.
- **`scripts/prompt.ps1`** — introduced in `f9022ab`, still the core one-shot orchestration launcher (its `--auto` flag and PromptFile support were added later, but the script's role is unchanged).
- **`webhook_receiver/` FastAPI app and the Caddy proxy** — introduced in `fe70456` / `8885d1f`, still the webhook ingress path documented in AGENTS.md.
- **`scripts/validate.ps1` / CI `validate.yml`** — introduced in `fabe75c`, still the mandatory lint/scan/test gate before every commit.

By contrast, the Beads execution loop, the dashboard, non-root container execution, and the `permission: allow` model are all comparatively recent (last five weeks of the ~two-month history) and each shows visible churn (multiple fix/refactor commits) shortly after introduction — consistent with features still being actively stabilized rather than settled.

## Growth trajectory

The commit-date histogram (4 commits in 2026-05, 204 in 2026-06, 148 in 2026-07 as of this analysis) shows the bulk of the codebase — the OpenCode server, webhook receiver, Beads loop, dashboard, and non-root hardening — was built in June, with July weighted more toward stabilization, security review responses (PR #49, #53, #54 review-comment-driven commits), and dispatch/label refactors rather than net-new subsystems. This pattern — rapid initial scaffolding followed by a longer hardening/refinement tail — is visible directly in the commit log but its cause (e.g., a deliberate phase change vs. organic slowdown) is not stated anywhere in the history and should be treated as inference, not fact.
