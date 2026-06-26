# Orchestration Dashboard

A real-time web UI for observing the three-tier Beads pipeline: bead DAG status, active agents, and a live event timeline. Served by `webhook-receiver` alongside the webhook receiver itself — no separate service.

## Open it

The dashboard runs inside `webhook-receiver` (internal port `8080`), fronted by the Caddy proxy on host `:80`.

| How you're running | URL |
|---------------------|-----|
| Docker Compose (default `:80`) | `http://localhost/dashboard` |
| Docker Compose + HTTPS overlay (`:443`) | `https://<WEBHOOK_SITE_ADDRESS>/dashboard` |
| Local dev (`uv run orchestrator-webhook`) | `http://localhost:8080/dashboard` |

> **Trusted-network only.** The dashboard exposes internal orchestration state and agent stdout/stderr. It has no authentication — run it only inside a trusted network, as you do for the rest of the receiver. The SSE endpoint is capped at **10 concurrent subscribers** to bound resource use.

## What you see

- **Summary cards** — Total / Ready / Blocked / Active / Closed / Halted bead counts.
- **Beads table** — every bead with a UI status badge, ID, title, type, priority, retry count, and elapsed time. Click a column header to sort; click a row to expand its description and live logs (stdout/stderr tabs).
- **Active Agents** — beads currently being processed, with attempt number and elapsed time.
- **Event Timeline** — recent system events (polls every 10 s, plus live push over SSE).
- **Top bar** — BeadsLoop running indicator, SSE connection status, and a clock.

The page auto-refreshes overview/beads every 10 s and active agents every 5 s; state-changing events push an immediate refresh over SSE.

### Bead UI status

| Status | Meaning |
|--------|---------|
| `active` | Currently being processed by a spawned agent |
| `ready` | Open, unblocked, awaiting pickup by `BeadsLoop` |
| `blocked` | Open but waiting on dependencies |
| `closed` | Completed and verified via `br show` |
| `halted` | Exceeded `BEADS_MAX_RETRIES`; needs human intervention |

An empty table with the message *"No beads found. Run /plan-to-beads to create the DAG."* means `.beads/` is not yet initialized — a normal "ready at will" state, not an error.

## HTTP API

All endpoints are JSON and live under `/api/dashboard`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard/overview` | Counts + BeadsLoop status (`running`, `poll_interval`, `max_retries`) |
| `GET` | `/api/dashboard/beads` | All beads enriched with UI status, retry count, elapsed time |
| `GET` | `/api/dashboard/active` | Beads currently being processed |
| `GET` | `/api/dashboard/events?limit=N` | Last `N` events (`limit` is clamped to `> 0`; default 100) |
| `GET` | `/api/dashboard/events/stream` | Server-Sent Events stream of live events (keepalive on idle) |
| `GET` | `/api/dashboard/beads/{bead_id}/logs?tail=N` | Most recent stdout/stderr log for a bead (`tail` clamped to `[1,2000]`, default 200) |

### Event types

The timeline and SSE stream carry these event `type`s:

| Type | When emitted |
|------|--------------|
| `webhook_received` | A signed GitHub webhook delivery was accepted |
| `dispatch_started` | An orchestration run was launched via `prompt.ps1` |
| `dispatch_completed` | An orchestration run exited |
| `bead_picked_up` | `BeadsLoop` selected a bead to process |
| `agent_spawned` | An isolated agent subprocess started for a bead |
| `agent_completed` | The agent subprocess exited (with status) |
| `bead_closed` | A bead was verified closed via `br show` |
| `bead_failed` | A bead attempt failed; will be retried |
| `bead_halted` | A bead exceeded max retries and is halted |

## Example

```bash
curl -s http://localhost/api/dashboard/overview | jq
curl -s "http://localhost/api/dashboard/beads/<bead-id>/logs?tail=50"
```

## How it works

- **Frontend** — `webhook_receiver/static/dashboard.html` (single self-contained HTML/CSS/JS file; no build step). Polled intervals + an SSE connection for live updates.
- **Backend** — `webhook_receiver/dashboard.py` defines the `/api/dashboard/*` JSON routes and serves the HTML page. Bead data comes from the `br`/`bvr` CLI (TTL-cached); live state comes from the in-process `BeadsLoop`; events come from the in-memory `EventStore` ring buffer.
- **Wiring** — `webhook_receiver/app.py` includes the dashboard routers when the app is built.

### Related config

The dashboard reflects these environment variables (set on `webhook-receiver`):

| Variable | Effect on dashboard |
|----------|---------------------|
| `BEADS_ENABLED` | When `false`, `BeadsLoop` is absent; overview shows `running: false` and no active agents |
| `BEADS_POLL_INTERVAL` | Shown as the loop's poll interval |
| `BEADS_MAX_RETRIES` | Shown as `max_retries`; drives `halted` state |
| `BEADS_WORKSPACE_ROOT` | Where `br`/`bvr` are invoked (bead data source) |
