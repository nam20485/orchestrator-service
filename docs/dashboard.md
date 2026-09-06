# Orchestration Dashboard

A real-time web UI for observing the three-tier Beads pipeline: bead DAG status, active agents, and a live event timeline. Served by `webhook-receiver` alongside the webhook receiver itself — no separate service.

## Open it

The dashboard runs inside `webhook-receiver` (container port `8080`), published to the host on **loopback only** at `127.0.0.1:8081`. It is deliberately *not* reachable through the Caddy site on host `:80` — that site is the public surface (the Tailscale Funnel target) and proxies only `POST /webhooks/github` and `GET /health`; every other path answers `404`.

| How you're running | URL |
| -------------------- | ----- |
| Docker Compose (host) | `http://127.0.0.1:8081/dashboard` |
| Docker Compose (another tailnet machine) | `https://<machine>.<tailnet>.ts.net:8443/dashboard` — see [Tailnet access](#tailnet-access) |
| Local dev (`uv run orchestrator-webhook`) | `http://localhost:8080/dashboard` |

The `http://localhost/dashboard` form no longer serves the dashboard: it returns `404` by design. If you have a bookmark for it, use `127.0.0.1:8081`.

> **Authentication is still required**, independently of the network path. The dashboard exposes internal orchestration state and agent stdout/stderr (which may include secrets or repo data), so every dashboard route is **gated behind a shared secret** (`DASHBOARD_TOKEN`):
>
> - **Disabled by default.** If `DASHBOARD_TOKEN` is unset, the entire dashboard surface returns `404` — nothing can be enumerated or exfiltrated.
> - **When set**, requests must present the token via one of:
>   - an `Authorization: Bearer <DASHBOARD_TOKEN>` header (programmatic / API clients),
>   - a `?token=<DASHBOARD_TOKEN>` query parameter, or
>   - a `dashboard_token` cookie.
> - Open the UI at `http://127.0.0.1:8081/dashboard?token=<DASHBOARD_TOKEN>`; the page persists the token as an HttpOnly/SameSite=Strict cookie so subsequent browser `fetch()` and SSE calls authenticate automatically. Prefer the HTTPS tailnet URL for anything beyond the host itself so the token never traverses the network in cleartext.
>
> The SSE endpoint is also capped at **10 concurrent subscribers** to bound resource use.

## Network access

Two layers, both required: the **path restriction in Caddy** keeps the dashboard
off the public ingress, and **`DASHBOARD_TOKEN`** still gates every route.

### Reachability matrix

| Surface | Public funnel (`:80`) | Host localhost | Tailnet | LAN |
| --------- | ----------------------- | ---------------- | --------- | ----- |
| `POST /webhooks/github` | ✅ (HMAC `OS_WEBHOOK_SECRET`) | ✅ | ✅ | ✅ |
| `GET /health` | ✅ | ✅ | ✅ | ✅ |
| `/dashboard`, `/api/dashboard/*`, `/simulator` | ❌ `404` | ✅ `127.0.0.1:8081` + token | ✅ `:8443` + token | ❌ refused |

### Can I bind to the Tailscale IP instead?

Partly, and it's worth knowing where the line is.

**The container cannot.** `tailscale0` is in the **host** network namespace; the
receiver has its own `eth0` on the Docker bridge, so no `WEBHOOK_HOST` value can
bind `100.64.0.0/10` — the address does not exist inside the container.

**A host-side publish does work**, and is a legitimate alternative:

```yaml
ports:
  - "127.0.0.1:8081:8080"
  - "100.103.219.1:8081:8080"   # verified: binds ONLY this address
```

Measured on this host: `ss -ltn` showed the socket bound to `100.103.219.1`
alone (no `0.0.0.0` spill), a real tailnet peer got `200`, and the LAN address
refused. So it is genuinely tailnet-only.

Three reasons this repo still uses loopback + Serve:

- **Portability.** The address is node-specific, so it cannot live in
  `compose.yaml` as a literal. Interpolating it (`"${TAILNET_IP}:8081:8080"`)
  **fails open**: an unset variable yields `":8081:8080"`, which Docker binds on
  *all interfaces* — the exact opposite of the goal, silently. A hardcoded
  literal also races `tailscaled` at startup and needs a recreate after IP
  churn.
- **No TLS, no hostname.** A peer reaches `http://100.103.219.1:8081`, so
  `request.url.scheme` is `http` and the dashboard cookie can never be
  `Secure`. Serve gives a stable `https://<machine>.<tailnet>.ts.net` endpoint.
- **ACL granularity.** Tailnet ACLs match on Tailscale hostnames/paths, which
  Serve exposes as first-class; a raw published port is all-or-nothing.

`network_mode: host` would also let the container bind the address but breaks
the service DNS this stack depends on
(`OPENCODE_SERVER_URL=http://orchestratorservice:4099`,
`reverse_proxy webhook-receiver:8080`).

### Why not allowlist by client address?

The socket peer address cannot express "localhost or tailnet, but not public"
for this topology:

- a tailnet peer arriving through `tailscale serve` is presented to the local
  listener as **`127.0.0.1`** (measured);
- **Funnel is the same.** `tailscaled` dials the target itself, so the peer at
  Caddy is `127.0.0.1` (measured: `ss -tan '( sport = :80 )'` →
  `ESTAB 127.0.0.1:80 127.0.0.1:41738`).
- `X-Forwarded-For` is the only thing that *might* separate them, and it is not
  dependable: Tailscale's behaviour here is undocumented and version-dependent
  (issue reports show Funnel's XFF holding a `100.x` or loopback value rather
  than the real public IP). Worse, Caddy trusts `X-Forwarded-For` from loopback
  by default, and host `:80` is published on **all** interfaces — so any LAN host
  could simply send `X-Forwarded-For: 100.1.2.3` and satisfy an address rule.

Splitting by **path** (Caddy) and **port** (loopback publish) needs none of that
trust, which is why `DASHBOARD_TOKEN` stays the second layer rather than the
only one.

> Optional follow-up: publishing Caddy as `127.0.0.1:80:80` would make Funnel the
> only network path to the proxy at all (tailscaled connects from loopback, so
> webhook delivery is unaffected) and would remove that header-spoofing route.

### Tailnet access

One host-side command, no repo change and no IP binding — `tailscaled` is
already on the host, so it can reach the loopback-published port and bring
tailnet peers to it:

```sh
tailscale serve --bg --https=8443 localhost:8081
```

Then from any tailnet peer: `https://<machine>.<tailnet>.ts.net:8443/dashboard?token=<DASHBOARD_TOKEN>`.

Verified on this host: the handler registers as its own entry labelled
`(tailnet only)`, the funnel's 443 handler is untouched, the listener binds only
`100.103.219.1:8443` and the Tailscale IPv6 ULA (nothing on `0.0.0.0`, LAN
refused), and `GET /health` / `GET /dashboard?token=…` over TLS both return
`200`.

> **Do not run `tailscale serve --bg 8081`.** The positional argument is the
> *target*; without `--https=<port>` it mounts on the default served port 443,
> which is the handler your funnel already points at `127.0.0.1:80`. Re-aiming
> it would publish the dashboard through the funnel *and* break webhook
> delivery. `tailscale serve status` shows the current handlers; 8443 must have
> its own.
>
> `serve` is tailnet-only — a port goes public only if you separately run
> `tailscale funnel` for it. That makes the choice of 8443 worth a moment's
> attention: Tailscale permits Funnel on only **443, 8443, and 10000**, so 8443 is
> one of the few ports where a stray `tailscale funnel --https=8443` would succeed
> and publish the dashboard to the internet. Check `tailscale serve status` after
> any funnel change and confirm the 8443 entry still reads `(tailnet only)`.

If you do not need tailnet access, skip this step: the dashboard then remains
host-local. To undo it: `tailscale serve --https=8443 off` (the form the CLI
prints; `tailscale serve clear --https=8443` errors and removes nothing, and
`tailscale serve clear` with **no** port wipes the whole Serve config *including
the funnel* — never use it here).

### Changing the public path allowlist

`deploy/caddy/Caddyfile` is the only place that decides what the public site
proxies. It is **baked into the caddy image** (`deploy/caddy/Dockerfile` copies
it), so an edit needs an image rebuild, not just a `compose up`:

```sh
docker compose -f compose.yaml -f compose.build.yaml build webhook-proxy
docker compose -f compose.yaml -f compose.build.yaml up -d webhook-proxy
```

`test/test-caddyfile-routes.sh` runs the real Caddyfile against a stub upstream
and fails if any dashboard/API path becomes reachable through the public site.

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
