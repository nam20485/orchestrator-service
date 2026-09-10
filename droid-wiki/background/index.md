# Background

`orchestrator-service` (root: `/home/nam20485/src/github/nam20485/orchestrator-service`) is the runtime member of a three-repo software factory: a GitHub template/runtime (this repo), a repo factory (`nam20485/workflow-launch2`), and a canonical workflow-definition store (`nam20485/agent-instructions`). This section explains why the current runtime looks the way it does and what real incidents shaped it.

- [Design Decisions](design-decisions.md) — the load-bearing architectural choices and the reasoning captured in source, docs, and commit history.
- [Pitfalls](pitfalls.md) — verified operational failure modes, their root causes, and the fixes/mitigations actually shipped.

## What the service does

Three containers, defined in `compose.yaml`:

- `orchestratorservice` — an OpenCode server (`opencode serve` on `:4099`) hosting agent sessions, built from `Dockerfile`.
- `webhook-receiver` — a FastAPI app (`webhook_receiver/app.py`) that validates GitHub App webhooks, dispatches orchestrator runs via `scripts/prompt.ps1`, and runs the `BeadsLoop` background thread (`webhook_receiver/beads_loop.py`).
- `webhook-proxy` — Caddy, terminating HTTP on host `:80` (and `:443` with the `compose.https.yaml` overlay) and proxying only `/webhooks/github` and `/health`; the dashboard is reached on the receiver's loopback-only `127.0.0.1:8081` publish instead.

The pipeline the service executes end-to-end (`README.md`):

| Phase | Actor | Output |
|-------|-------|--------|
| Ideation | `/perfect-idea` skill (PM agent) | `application_plan.md` |
| Planning | `/plan-to-beads` skill (Scrum agent) | `.beads/` DAG |
| Execution | `BeadsLoop` background thread | Working software + PRs |

## Primary sources used for this section

- `AGENTS.md`
- `README.md`
- `docs/testing-approach.md`
- `docs/deployment-compose.md`
- `docs/environment-variables.md`
- `docs/tool-memory.md`
- `docs/orchestrator-run-logs.md`
- `webhook_receiver/{runner,watchdog,beads_loop,dashboard,workspace}.py`
- `Dockerfile`, `Dockerfile.webhook`, `compose.yaml`
- `.github/workflows/validate.yml`
- Explicitly marked historical/deferred material under `plan_docs/.archived/` and `plan_docs/.deferred/`
