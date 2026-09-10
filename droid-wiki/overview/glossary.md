# Glossary

This page defines terms that recur in the runtime, agent configuration, and operator documentation. The implementation vocabulary is rooted in `webhook_receiver/`, the shipped OpenCode configuration in `image/.opencode/`, and the Compose stack in `compose.yaml`.

| Term | Meaning |
| --- | --- |
| **Bead** | A task node in a project's Beads dependency graph. `webhook_receiver/beads_loop.py` selects ready beads and checks their closure. |
| **BeadsLoop** | The optional daemon thread that scans workspaces and executes one eligible bead per project at a time. |
| **bvr** | The Beads viewer CLI. The loop prefers its graph-aware next-task recommendation and can export the pages bundle served by the dashboard. |
| **Dashboard** | Token-gated HTML, JSON, and SSE surfaces implemented in `webhook_receiver/dashboard.py`. |
| **Direct-body dispatch** | A special `gh-issue-tracking:direct-body` trigger that uses an issue body as the orchestrator prompt. `webhook_receiver/filters.py` restricts it to configured trusted senders. |
| **Dispatch** | A webhook-originated OpenCode run started by `webhook_receiver/runner.py`. A dispatch has a prompt, stdout/stderr files, and a manifest. |
| **EventStore** | An in-memory ring buffer with SSE fan-out in `webhook_receiver/event_store.py`. It is not a durable event log. |
| **Manifest** | A JSON sidecar written by `webhook_receiver/runner.py` for a dispatch. It records identity, lifecycle, exit code, classification, and detected tools. |
| **OpenCode server** | The agent server exposed by `orchestratorservice` on port 4099. Its global configuration comes from `image/.opencode/opencode.json`. |
| **Orchestrator** | The default OpenCode agent configured in `image/.opencode/opencode.json`; it handles a dispatched prompt and delegates specialist work. |
| **Project workspace** | A directory such as `/workspace/owner-repo` that holds one downstream clone, its `.beads/` graph, and local worktrees. Path construction is guarded by `webhook_receiver/workspace.py`. |
| **Run narrative** | A summarized status, timeline, and tool-call count generated from captured logs by `webhook_receiver/run_narrative.py`. |
| **Watchdog** | The activity-aware supervisor in `webhook_receiver/watchdog.py`. It can terminate idle, error-looping, permission-blocked, or overlong runs. |
| **WebhookStore** | A bounded JSON-backed delivery record in `webhook_receiver/webhook_store.py`. It lets the dashboard link accepted webhooks to dispatch runs. |
| **Worktree** | An isolated Git checkout under `.worktrees/<bead-id>` used for an individual bead branch. |

For the complete execution sequence, see [Architecture](architecture.md). For definitions tied to dashboard views and APIs, see [Observability](../features/observability.md).
