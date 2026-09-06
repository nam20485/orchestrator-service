# Orchestrator service

`orchestrator-service` is a single-host runtime for running OpenCode agents against downstream repositories. It accepts signed GitHub issue-label webhooks, starts an orchestrator session in a per-project workspace, and can execute a Beads dependency graph through isolated Git worktrees.

The runtime is a Docker Compose stack with an OpenCode server, a FastAPI webhook receiver, and a Caddy proxy. Python code in `webhook_receiver/` owns HTTP handling, run supervision, dashboard data, and Beads automation. The agent configuration shipped into the image lives in `image/.opencode/`.

## What it runs

| Component | Responsibility | Main source |
| --- | --- | --- |
| OpenCode server | Hosts configured agent sessions on port 4099. | `Dockerfile`, `image/.opencode/opencode.json` |
| Webhook receiver | Verifies deliveries, filters triggers, renders prompts, launches runs, and hosts the dashboard. | `webhook_receiver/app.py`, `webhook_receiver/runner.py` |
| Beads loop | Selects ready DAG nodes, creates worktrees, invokes agents, and checks task closure. | `webhook_receiver/beads_loop.py` |
| Caddy proxy | Exposes only the webhook endpoint and the health probe on the host HTTP/TLS edge. | `compose.yaml`, `deploy/caddy/Caddyfile` |

## Typical flow

1. A downstream repository emits an `issues.labeled` delivery.
2. `webhook_receiver/app.py` checks the HMAC signature and asks `webhook_receiver/filters.py` whether the label, event, and actor can trigger a run.
3. The receiver renders `webhook_receiver/orchestration_prompt.jinja2.md`, ensures a project workspace under `/workspace`, and starts `scripts/prompt.ps1` in a FastAPI background task.
4. `scripts/prompt.ps1` attaches `opencode run` to the OpenCode service, with the selected model, agent, workspace, and automatic approval flags.
5. Separately, `webhook_receiver/beads_loop.py` discovers initialized project DAGs and works through ready beads when enabled.

For component relationships and boundaries, read [Architecture](architecture.md). For a local setup, read [Getting started](getting-started.md).

## Repository map

```text
webhook_receiver/       FastAPI application and orchestration runtime
image/.opencode/        OpenCode configuration, agents, commands, and skills
scripts/                Dispatch, validation, image-entrypoint, and GitHub helpers
deploy/caddy/           Caddy image and reverse-proxy configuration
test/                   Bash and Pester checks
tests/                  Pytest unit and integration coverage
docs/                   Operational documentation and generated OpenAPI schema
.github/workflows/      Validation, image publishing, security, and review automation
```

## Read next

- [Architecture](architecture.md), for the runtime data paths and state boundaries.
- [Webhook dispatch](../features/webhook-dispatch.md), for admission and prompt construction.
- [Beads execution](../features/beads-execution.md), for automated DAG work.
- [Deployment](../deployment.md), for Compose modes and operational limits.
