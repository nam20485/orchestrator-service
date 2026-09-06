# Webhook proxy

Active contributors: Nathan Miller

`webhook-proxy` is a Caddy reverse proxy that terminates the public HTTP/HTTPS edge and forwards only the GitHub webhook endpoint and the health probe to `webhook-receiver` on its internal port `8080`; every other path is answered locally with `404 Not Found`. It has no application logic of its own — its entire behavior is the set of mutually exclusive `handle` blocks in `deploy/caddy/Caddyfile`. This site is the public surface (the Tailscale Funnel target), so the token-gated dashboard, the dashboard API, and the simulator are deliberately *not* proxied here: they are reached through the receiver's loopback-only publish at `127.0.0.1:8081` (optionally tailnet-served), as described in `docs/dashboard.md`. `webhook-proxy` publishes host `80:80` (plus `443` with the `compose.https.yaml` overlay); unlike the receiver's loopback-only publish, that listener is reachable from off the host, which is why its path allowlist is the security boundary.

## Key source files

| File | Role |
| --- | --- |
| `deploy/caddy/Caddyfile` | The whole proxy configuration: a single `{$WEBHOOK_SITE_ADDRESS}` site whose **path-restricted** `handle` blocks forward only `/webhooks/github` and `/health` to `webhook-receiver:8080`, with a catch-all `handle { respond "Not Found" 404 }` for everything else (`/dashboard*`, `/api/dashboard/*`, `/simulator`, `/docs`, `/openapi.json`, …). The 404 shape matches the receiver's own "route does not exist" response so the public surface enumerates nothing. `WEBHOOK_SITE_ADDRESS` selects HTTP-only (`:80`) or a real hostname (triggers Caddy's automatic Let's Encrypt HTTPS). The file is **baked into the image** by `deploy/caddy/Dockerfile`, so an edit needs an image rebuild, not just a `compose up`. |
| `deploy/caddy/Dockerfile` | Builds from the pinned `caddy:2.10.0-alpine` base (digest-pinned), creates a non-root `caddy` system user (the upstream image ships no non-root user), and grants privileged-port binding via `setcap cap_net_bind_service=+ep /usr/bin/caddy`. |
| `deploy/caddy/caddy-entrypoint.sh` | Container entrypoint: starts as root, chowns the `caddy_data`/`caddy_config` volumes to `caddy:caddy` on first mount if the owner differs, then drops privileges with `su-exec caddy` before executing `caddy run`. |
| `compose.yaml` (`webhook-proxy` service) | Publishes host `80:80`, adds `cap_add: CAP_NET_BIND_SERVICE`, mounts the `caddy_data`/`caddy_config` named volumes, and passes `WEBHOOK_SITE_ADDRESS` through to the Caddyfile. Deliberately omits `no-new-privileges` and `cap_drop: ALL` — the former would block the file capability from taking effect at `execve`, and the latter would remove the `SETUID`/`SETGID`/`CHOWN` capabilities the root entrypoint needs. |
| `compose.https.yaml` | Optional overlay that adds `443:443` to this service, for TLS via Caddy's automatic HTTPS when `WEBHOOK_SITE_ADDRESS` is a real hostname. Not meant to be combined with a Tailscale Funnel, which would also try to bind host `:443`. |

## Startup and privilege drop

```mermaid
graph TD
    A[Container starts as root<br/>no USER directive] --> B[caddy-entrypoint.sh]
    B --> C{caddy_data / caddy_config<br/>owner == caddy UID?}
    C -- no --> D[chown -R caddy:caddy]
    C -- yes --> E
    D --> E[su-exec caddy caddy run<br/>--config /etc/caddy/Caddyfile]
    E --> F["kernel reapplies cap_net_bind_service<br/>file capability at execve"]
```

The file capability set at build time (`setcap cap_net_bind_service=+ep /usr/bin/caddy`) is what lets the non-root `caddy` process bind `:80`/`:443` after the `su-exec` drop; the kernel reapplies file capabilities at `execve` regardless of the caller's UID, so the privilege drop does not strip the ability to bind those ports.

The image's `HEALTHCHECK` probes Caddy's local admin API (`http://127.0.0.1:2019/config/`) rather than the proxied site, since `WEBHOOK_SITE_ADDRESS` may be a hostname that needs DNS/ACME to resolve and would not be reachable in every environment.

## Integration points

- **Webhook receiver**: the sole downstream target, reached through the `reverse_proxy webhook-receiver:8080` directive inside each proxied `handle` block. The proxy is necessarily route-aware: it enumerates the receiver's `/webhooks/github` and `/health` path prefixes to decide what to forward, and answers `404` locally for everything else (dashboard pages and API, simulator, docs) instead of forwarding it. That is a *path* decision only — `webhook-proxy` has no awareness of the receiver's dispatch logic, and HMAC verification and `DASHBOARD_TOKEN` gating still happen downstream on every path the dashboard is reached from.
- **GitHub**: the public entry point for `issues:labeled` webhook deliveries; the receiver's HMAC verification (`OS_WEBHOOK_SECRET`) happens downstream of this proxy, not here.
- **`caddy_data` / `caddy_config` volumes**: persist Caddy's internal state (including any Let's Encrypt certificates and account data) across container restarts.
- **`compose.https.yaml`**: the only way to expose host `:443` in this stack; without it, `webhook-proxy` serves HTTP only, which is the expected mode when a Tailscale Funnel or another external TLS terminator sits in front of it.

## Modification entry points

- Change the site address, add a path to the public allowlist, or add global Caddy options (for example an ACME account email): `deploy/caddy/Caddyfile`. `test/test-caddyfile-routes.sh` runs that file against a stub upstream and fails if any dashboard/API path becomes reachable through the public site.
- Change the base image, installed packages, or the non-root user/capability setup: `deploy/caddy/Dockerfile`.
- Change startup ownership fixups or the privilege-drop mechanism: `deploy/caddy/caddy-entrypoint.sh`.
- Change published ports, capabilities, or volumes: `compose.yaml` and `compose.https.yaml`.

## Related pages

- [Services](index.md)
- [Webhook receiver](webhook-receiver.md), the only service this proxy forwards to.
- [Architecture](../overview/architecture.md), for the full runtime data-flow picture.
