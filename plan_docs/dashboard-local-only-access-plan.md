# Plan: Dashboard local/tailnet-only access (funnel exposes webhook only)

**Status:** IMPLEMENTED — 2026-09-05
**Date:** 2026-09-05
**Trigger:** Security review of the Tailscale Funnel ingress for orchestrator-service.
**Deviation from this plan:** step 3's command was wrong as written and was
corrected before implementation — see "Tailnet access" below.

## Goal

The public internet (via Tailscale Funnel) may only reach the GitHub webhook
endpoint `POST /webhooks/github` (plus `/health`). All dashboard pages and
dashboard APIs must be reachable **only** from localhost or hosts on the
Tailscale network (tailnet) — never through the funnel.

## Key constraint: the client address cannot separate local from public

Measured on this host (tailscale 1.102.3):

- `tailscale funnel 80` terminates TLS inside `tailscaled` and **dials
  `127.0.0.1:80` itself**, so the socket peer at Caddy is loopback
  (measured: `ss -tan '( sport = :80 )'` → `ESTAB 127.0.0.1:80 127.0.0.1:41738`).
- A tailnet peer arriving through `tailscale serve` is presented to the local
  listener as `127.0.0.1` too, and a locally-run tunnel (`ngrok http 80`, an
  option this repo documents) also connects from `127.0.0.1`.
- `X-Forwarded-For` is unreliable as a substitute: Tailscale's behaviour is
  undocumented and version-dependent (reports show Funnel's XFF holding a `100.x`
  or loopback value, not the public IP), and Caddy trusts XFF from loopback while
  host `:80` is published on all interfaces — so a LAN host can forge it.

Consequence: neither the peer address nor a forwarded header can express
"localhost + tailnet, not public". So the separation is by **port and path**:

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
tailscale serve --bg --https=8443 localhost:8081
```

Gives `https://<machine>.<tailnet>.ts.net:8443` for tailnet peers only.

> **Do not run `tailscale serve --bg 8081`** (the command this plan originally
> specified). Serve's positional argument is the *target*, and without an
> explicit `--https=<port>` it mounts on the default served port **443** — the
> same handler the funnel already uses. `tailscale serve status` on this host
> showed exactly one handler, `https://<host>.ts.net (Funnel on) / proxy
> http://127.0.0.1:80`; pointing `serve --bg 8081` at it would have silently
> re-aimed the funnel target at the dashboard, exposing the dashboard publicly
> *and* breaking GitHub delivery. Serving the dashboard on a distinct port
> (`--https=8443`) keeps the two handlers separate, and funnel is not enabled
> for 8443, so 8443 stays tailnet-only.

- `serve` is **tailnet-only**; a port becomes public only if `tailscale funnel`
  is separately enabled for it. Never enable funnel for 8443.
- Serve terminates TLS and proxies plain HTTP to `127.0.0.1:8081`, so
  `127.0.0.1:8081` itself never leaves the host.
- Measured nit (2026-09-05): over the Serve TLS path the app still emits
  `Set-Cookie: dashboard_token=…; HttpOnly; Path=/; SameSite=strict` **without**
  `Secure`. `tailscaled` does send `X-Forwarded-Proto: https` (it sets it whenever
  the inbound connection had TLS), but uvicorn only applies forwarding headers
  from `forwarded_allow_ips` (default `127.0.0.1`) and the hop into the container
  is NAT'd through `docker-proxy`, so the peer the app sees is the bridge gateway,
  not loopback — the header is ignored and `request.url.scheme` stays `http`.
  Transport is encrypted regardless (TLS to tailscaled + WireGuard to the peer),
  so only the cookie attribute is affected. Closing it means setting
  `FORWARDED_ALLOW_IPS` to the compose gateway address — narrow it deliberately,
  since trusting headers from the whole bridge subnet would let anything on that
  network assert the scheme.
- The funnel stays on port 80 only: `tailscale funnel --bg 80` (webhook).
- Undo: `tailscale serve --https=8443 off`. Do **not** use `tailscale serve
  clear` without a port — it wipes the whole Serve config including the funnel.

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
| Host (no repo change)       | `tailscale serve --bg --https=8443 localhost:8081`; funnel stays on 80 |
| `test/test-caddyfile-routes.sh` | **new** functional test: real Caddyfile vs a stub upstream that answers 200 to everything |
| `test/test-compose-config.sh` | asserts every receiver publish has `host_ip == 127.0.0.1`, both compose files |
| `scripts/validate.ps1`      | registers the route test in `-Test` (CI's test job runs `-Test`, so it mirrors) |

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

### Verification results (measured 2026-09-05, this host)

| Probe | Expected | Measured |
| ----- | -------- | -------- |
| `http://localhost/health` | 200 | 200 ✅ |
| `http://localhost/dashboard` | 404 | 404 ✅ |
| `http://localhost/api/dashboard/overview` | 404 | 404 ✅ |
| `https://<funnel-host>/dashboard` | 404 | 404 ✅ |
| `http://127.0.0.1:8081/health` | 200 | 200 ✅ |
| `http://127.0.0.1:8081/dashboard` (no token) | 401 | 401 ✅ |
| `http://127.0.0.1:8081/dashboard?token=…` | 200 | 200 ✅ |
| `https://<host>.ts.net:8443/dashboard?token=…` (Serve) | 200 | 200 ✅ |
| `ss -ltn` for `:8443` | tailscale addrs only | `100.103.219.1` + `fd7a:…` only ✅ |
| `http://192.168.1.29:8081`, `:8443` (LAN) | refused | refused ✅ |
| `tailscale serve status` after adding 8443 | funnel 443 entry intact | intact, 8443 `(tailnet only)` ✅ |

Still requires a second device / GitHub to confirm: step 3 (a real GitHub
delivery → 202) and step 5 from an actual tailnet peer. The `:8443` result above
was obtained from this host via `--resolve <ts.net-host>:8443:100.103.219.1`,
which exercises tailscaled's TLS terminator and the real upstream, but is not a
substitute for a peer connection.

## Alternatives considered and rejected

- **IP allowlist at Caddy/app**: the peer address cannot separate local from
  tailnet from public (see Key constraint); an `X-Forwarded-For` allowlist could,
  but makes a client-influenced header the security boundary.
- **Leave `DASHBOARD_TOKEN` unset on the funnel deployment**: disables the
  dashboard everywhere; does not meet "reachable from localhost/tailnet".
- **Publish receiver `:8080` on all interfaces**: re-exposes the dashboard to
  LAN/public — the opposite of the goal.
- **Publish the receiver on the Tailscale IP** (`- "100.103.219.1:8081:8080"`):
  verified to work and to stay tailnet-only (binds just that address, LAN
  refused, a real peer got `200`). Rejected anyway: the address is node-specific
  so it cannot be a compose literal, and interpolating it fails **open** (an
  unset variable yields `":8081:8080"` = all interfaces). Serve reaches the same
  goal with a stable hostname, TLS, and no IP coupling. Detail:
  `docs/dashboard.md#can-i-bind-to-the-tailscale-ip-instead`.
- **Second Caddy site block on an internal port instead of publishing the
  receiver**: works, but adds a config surface to achieve what a loopback
  port publish already does; rejected for simplicity.
