# Orchestration Dashboard

A real-time web UI for observing the three-tier Beads pipeline: bead DAG status, active agents, and a live event timeline. Served by `webhook-receiver` alongside the webhook receiver itself — no separate service.

## Open it

The dashboard runs inside `webhook-receiver` (internal port `8080`), fronted by the Caddy proxy on host `:80`.

| How you're running | URL |
|---------------------|-----|
| Docker Compose (default `:80`) | `http://localhost/dashboard` |
| Docker Compose + HTTPS overlay (`:443`) | `https://<WEBHOOK_SITE_ADDRESS>/dashboard` |
| Local dev (`uv run orchestrator-webhook`) | `http://localhost:8080/dashboard` |

> **Authentication required.** The dashboard exposes internal orchestration state and agent stdout/stderr (which may include secrets or repo data). Because Caddy proxies the whole `webhook-receiver` surface (not just `/webhooks/github`), every dashboard route is **gated behind a shared secret** (`DASHBOARD_TOKEN`):
> - **Disabled by default.** If `DASHBOARD_TOKEN` is unset, the entire dashboard surface returns `404` — nothing can be enumerated or exfiltrated through the proxy.
> - **When set**, requests must present the token via one of:
>   - an `Authorization: Bearer <DASHBOARD_TOKEN>` header (programmatic / API clients),
>   - a `?token=<DASHBOARD_TOKEN>` query parameter, or
>   - a `dashboard_token` cookie.
> - Open the UI at `http://localhost/dashboard?token=<DASHBOARD_TOKEN>`; the page persists the token as an HttpOnly/SameSite=Strict cookie so subsequent browser `fetch()` and SSE calls authenticate automatically. Use HTTPS (the `:443` overlay) so the token/cookie never traverse the network in cleartext.
>
> The SSE endpoint is also capped at **10 concurrent subscribers** to bound resource use.

## What you see

- **Summary cards** — Total / Ready / Blocked / Active / Closed / Halted bead counts.
- **Beads table** — every bead with a UI status badge, ID, title, type, priority, retry count, and elapsed time. Click a column header to sort; click a row to expand its description and live logs (stdout/stderr tabs).
- **Active Agents** — beads currently being processed, with attempt number and elapsed time.
- **Event Timeline** — recent system events (loaded once on page open, then kept up to date live via SSE).
- **Top bar** — BeadsLoop running indicator, SSE connection status, and a clock.

The page auto-refreshes overview, beads, and active agents **together** on a single configurable interval (default **5 s**, adjustable via the **Settings** panel — toggle Auto-refresh and set the interval in seconds). The event timeline loads once on page open and is then kept current by live SSE pushes; state-changing events also trigger an immediate refresh of the relevant panels.

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

All endpoints are JSON. Every endpoint requires authentication (see the note above): pass `DASHBOARD_TOKEN` as a `Bearer` token or `?token=` query parameter. Routes that accept a `?project=<slug>` query operate on that project's `.beads/`; without it they use the default workspace.

### Beads / DAG (`/api/dashboard/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard/overview` | Counts + BeadsLoop status (`running`, `poll_interval`, `max_retries`) |
| `GET` | `/api/dashboard/beads` | All beads enriched with UI status, retry count, elapsed time |
| `GET` | `/api/dashboard/beads/{bead_id}` | Single bead detail |
| `GET` | `/api/dashboard/graph` | Bead dependency DAG (for graph rendering) |
| `GET` | `/api/dashboard/active` | Beads currently being processed |
| `GET` | `/api/dashboard/beads/{bead_id}/logs?tail=N` | Most recent stdout/stderr log for a bead (`tail` clamped to `[1,2000]`, default 200) |

### Events (`/api/dashboard/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard/events?limit=N` | Last `N` events (`limit` clamped to `> 0`; default 100) |
| `GET` | `/api/dashboard/events/stream` | Server-Sent Events stream of live events (keepalive on idle; capped at 10 concurrent subscribers) |

### Runs (`/api/dashboard/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard/runs` | List of dispatch runs (per-run manifests) |
| `GET` | `/api/dashboard/runs/{stem}/logs?tail=N` | A run's captured stdout/stderr (`tail` clamped to `[1,4000]`, default 400) |
| `GET` | `/api/dashboard/runs/{stem}/narrative` | Synthesized narrative timeline of a run + its status |
| `GET` | `/api/dashboard/run-events?stem=<stem>` | Typed tool-stream glyph events for a run (defaults to newest run) |

### Webhooks (`/api/dashboard/*`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboard/webhooks?limit=N` | Recent signed webhook deliveries (default 500) |
| `GET` | `/api/dashboard/webhooks/{delivery_id}` | A single webhook delivery by `X-GitHub-Delivery` id |

### bvr pages bundle (`/api/dashboard/*`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/dashboard/pages/refresh` | Force-regenerate the `bvr` static-pages bundle for the project |

### HTML pages and static pages (browser-facing)

These render the dashboard UI rather than returning JSON (all still token-gated):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/dashboard` | Main dashboard page |
| `GET` | `/dashboard/bead/{bead_id}` | Per-bead detail page |
| `GET` | `/dashboard/runs` | Runs list page |
| `GET` | `/dashboard/runs/{stem}` | Single-run detail page |
| `GET` | `/dashboard/events` | Events timeline page |
| `GET` | `/dashboard/webhooks` | Webhook deliveries page |
| `GET` | `/dashboard/pages/` | Index of the generated `bvr` static-pages bundle |
| `GET` | `/dashboard/pages/{file_path}` | A file from the `bvr` static-pages bundle |

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
# DASHBOARD_TOKEN is required for every request (or the endpoint returns 404/401).
export DASHBOARD_TOKEN="your-secret-token"
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  http://localhost/api/dashboard/overview | jq
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  "http://localhost/api/dashboard/beads/<bead-id>/logs?tail=50"
```

## How it works

- **Frontend** — `webhook_receiver/static/dashboard.html` (single self-contained HTML/CSS/JS file; no build step). Polled intervals + an SSE connection for live updates.
- **Backend** — `webhook_receiver/dashboard.py` defines the `/api/dashboard/*` JSON routes and serves the HTML page. Bead data comes from the `br`/`bvr` CLI (TTL-cached); live state comes from the in-process `BeadsLoop`; events come from the in-memory `EventStore` ring buffer.
- **Wiring** — `webhook_receiver/app.py` includes the dashboard routers when the app is built.

### Related config

The dashboard reflects these environment variables (set on `webhook-receiver`):

| Variable | Effect on dashboard |
|----------|---------------------|
| `DASHBOARD_TOKEN` | **Required to enable the dashboard.** Unset → every dashboard route returns `404`. When set, all routes authenticate via `Bearer` header / `?token=` / `dashboard_token` cookie. |
| `BEADS_ENABLED` | When `false`, `BeadsLoop` is absent; overview shows `running: false` and no active agents |
| `BEADS_POLL_INTERVAL` | Shown as the loop's poll interval |
| `BEADS_MAX_RETRIES` | Shown as `max_retries`; drives `halted` state |
| `BEADS_WORKSPACE_ROOT` | Where `br`/`bvr` are invoked (bead data source) |

> Set `DASHBOARD_TOKEN` on the `webhook-receiver` service (e.g. `export DASHBOARD_TOKEN=...` before `docker compose up`, or pass it via your Compose environment). Treat it as a secret: never commit it, and only use it over HTTPS.
