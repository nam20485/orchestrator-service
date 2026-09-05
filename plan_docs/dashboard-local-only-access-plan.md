# Plan: Dashboard local/tailnet-only access (funnel exposes webhook only)

**Status:** PLANNED — not implemented
**Date:** 2026-09-05
**Trigger:** Security review of the Tailscale Funnel ingress for orchestrator-service.

## Goal

The public internet (via Tailscale Funnel) may only reach the GitHub webhook
endpoint `POST /webhooks/github` (plus `/health`). All dashboard pages and
dashboard APIs must be reachable **only** from localhost or hosts on the
Tailscale network (tailnet) — never through the funnel.

## Key constraint: funnel traffic appears as localhost

`tailscale funnel 80` terminates TLS inside tailscaled and proxies to
`127.0.0.1:80` on the host. Consequence: **every funnel request arrives at
Caddy with source address 127.0.0.1**, indistinguishable from a local client
by IP. Therefore:

- IP-based allowlisting in Caddy or in the FastAPI app **cannot** separate
  "funnel" from "local".
- The separation must be by **port and path**, not by source IP:
  - host `:80` (Caddy, the funnel target) → webhook paths only, 404 for the rest.
  - host `127.0.0.1:8081` (receiver published loopback-only) → full app
    including dashboard, for localhost and tailnet access.

## Current state (before changes)

- `deploy/caddy/Caddyfile` proxies **all** paths to `webhook-receiver:8080`:
  ```caddyfile
  {$WEBHOOK_SITE_ADDRESS} {
      reverse_proxy webhook-receiver:8080
  }
  ```
- `compose.yaml` publishes Caddy on host `:80` (comment explicitly names
  Tailscale Funnel as a consumer) and only `expose`s the receiver's `8080`
  (not published to the host).
- Dashboard routes (`/dashboard`, `/api/*`, `/events`, `/webhooks` list page,
  run-detail pages, simulator) live in the same FastAPI app as the webhook.
- `DASHBOARD_TOKEN` (webhook_receiver/auth.py) is fail-closed: unset → 404
  "Dashboard is disabled"; set → requires Bearer header / `?token=` /
  `dashboard_token` cookie (constant-time compare).

## Planned changes

### 1. `deploy/caddy/Caddyfile` — path-restrict the public site

Only the webhook delivery endpoint and the health probe are proxied; every
other path gets 404 so the public surface enumerates nothing:

```caddyfile
{$WEBHOOK_SITE_ADDRESS} {
    # Public surface (funnel target): webhook delivery + health only.
    handle /webhooks/github {
        reverse_proxy webhook-receiver:8080
    }
    handle /health {
        reverse_proxy webhook-receiver:8080
    }
    # Dashboard pages/APIs, simulator, everything else: hidden publicly.
    respond 404
}
```

Notes:

- `respond 404` (not 403) keeps the surface indistinguishable from a
  non-existent route.
- The receiver's container healthcheck used by
  `depends_on: condition: service_healthy` runs **inside** the container, not
  through Caddy — restricting Caddy paths does not affect orchestration.
- The simulator (`WEBHOOK_ENABLE_SIMULATOR=1`) also becomes unreachable
  publicly as a side effect — an improvement.

### 2. `compose.yaml` — publish the receiver on loopback only

Give localhost (and tailnet, via step 3) a direct path to the full app that
bypasses the path-restricted Caddy:

```yaml
webhook-receiver:
  ports:
    - "127.0.0.1:8081:8080"   # loopback-only: dashboard/API for local + tailnet serve
```

- Loopback bind → unreachable from LAN/public interfaces; port 8081 is never
  funneled.
- Keep the existing `expose: "8080"` (compose-internal) unchanged.
- The same edit applies to `compose.development.yaml`.

### 3. Tailnet access — host-side command, no repo change

On the Docker host:

```sh
tailscale serve --bg 8081
```

- `serve` is **tailnet-only** (it is never exposed publicly unless
  `tailscale funnel` is separately enabled for the same port — do not enable it
  for 8081).
- Provides automatic HTTPS at `https://<machine>.<tailnet>.ts.net`, so the
  dashboard cookie's `secure` flag (auth.py sets it for https) works.
- The funnel stays on port 80 only: `tailscale funnel --bg 80` (webhook).

### 4. `DASHBOARD_TOKEN` remains set (defense in depth)

- Unchanged behavior: 404 when unset, 401 without a valid token when set.
- Tailnet-only reachability + token = two independent layers.

## Resulting access matrix

| Path                  | Public funnel `:80`            | localhost                    | tailnet                            |
| --------------------- | ------------------------------ | ---------------------------- | ---------------------------------- |
| `/webhooks/github`   | ✅ (HMAC `OS_WEBHOOK_SECRET`) | ✅ via `:80` or `:8081`     | ✅                                  |
| `/health`            | ✅                             | ✅                           | ✅                                  |
| Dashboard + `/api/*` | ❌ 404                         | ✅ `127.0.0.1:8081` + token  | ✅ `https://…ts.net` + token       |
| Simulator            | ❌ 404                         | ✅ `:8081` (token-gated)     | ✅ (token-gated)                    |

## Files touched (when implemented)

| File                        | Change                                                       |
| --------------------------- | ------------------------------------------------------------ |
| `deploy/caddy/Caddyfile`    | Path-restrict public site; 404 fallback                      |
| `compose.yaml`              | `webhook-receiver.ports: ["127.0.0.1:8081:8080"]`            |
| `compose.development.yaml`  | Same loopback publish                                        |
| Host (no repo change)       | `tailscale serve --bg 8081`; funnel stays on 80              |

`compose.https.yaml` inherits the Caddyfile restriction automatically; no edit
needed.

## Verification steps (after implementation)

1. `docker compose config` parses; `docker compose up -d` healthy
   (receiver healthcheck unaffected — internal).
2. Public: `curl -i https://<funnel-host>/dashboard` → **404**;
   `curl -i https://<funnel-host>/health` → **200**.
3. Public webhook still works: GitHub repo → Settings → Webhooks →
   Recent Deliveries show 202/200 after a test delivery; unsigned POST to
   `/webhooks/github` → rejected by signature check.
4. Localhost:
   `curl -i -H "Authorization: Bearer $DASHBOARD_TOKEN" http://127.0.0.1:8081/dashboard`
   → **200**.
5. Tailnet device: open `https://<machine>.<tailnet>.ts.net/dashboard` →
   loads (token prompt/cookie flow works, secure cookie over https).
6. Negative: from a non-tailnet external host (e.g. phone on mobile data),
   both `https://<funnel-host>/dashboard` (404) and `http://<host-LAN-IP>:8081`
   (connection refused — loopback bind) fail.
7. From another machine on the LAN (not tailnet): `:8081` refused, `:80`
   dashboard paths 404.

## Alternatives considered and rejected

- **IP allowlist at Caddy/app**: impossible — funnel traffic is sourced from
  127.0.0.1 by tailscaled (see key constraint).
- **Leave `DASHBOARD_TOKEN` unset on the funnel deployment**: disables the
  dashboard everywhere; does not meet "reachable from localhost/tailnet".
- **Publish receiver `:8080` on all interfaces**: re-exposes the dashboard to
  LAN/public — the opposite of the goal.
- **Second Caddy site block on an internal port instead of publishing the
  receiver**: works, but adds a config surface to achieve what a loopback
  port publish already does; rejected for simplicity.
