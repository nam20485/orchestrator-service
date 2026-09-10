# Webhook Authentication: Security Options for the Orchestrator GitHub App

**Status:** Analysis (no implementation proposed without approval)
**Scope:** Inbound webhook delivery to `webhook_receiver/app.py`
**Authoritative sources:** `webhook_receiver/github.py`, `webhook_receiver/app.py`, `webhook_receiver/auth.py`, `webhook_receiver/config.py`

---

## 1. Context

The orchestrator exposes a GitHub App webhook endpoint at `/webhooks/github`.
Inbound delivery is authenticated by verifying the `X-Hub-Signature-256`
header against the raw request body using HMAC-SHA256 with the shared
secret `OS_WEBHOOK_SECRET` (`webhook_receiver/github.py:13-28`,
`webhook_receiver/app.py:233-250`).

This is GitHub's recommended mechanism and is cryptographically strong as
long as the secret stays private. The current code also performs:

- Body-size cap (`cfg.max_body_bytes`) before parsing.
- Strict slug/branch allowlists before any shell-handoff.
- HTTPS-only clone URL validation.

## 2. Scope of the Question

The question was whether **device-flow auth** or other authentication
mechanisms can be added to make **installation** of the GitHub App more
secure.

## 3. Findings

### 3.1 Device-flow auth (OAuth 2.0 RFC 8628) — NOT APPLICABLE

Device flow is designed for user-interactive authorization from
input-constrained devices that lack a browser. The webhook receiver is
a GitHub-to-server pipeline: GitHub signs the delivery server-side and
posts it; there is no human in the loop to type a code or to interactively
grant a token. Adding device flow would introduce browser steps for
something that does not require them.

### 3.2 Other OAuth flows — NOT APPLICABLE FOR DELIVERY AUTH

OAuth user-to-server and app-to-app flows authenticate the **caller**
of the GitHub REST/GraphQL API. Webhook delivery is the inverse
direction: GitHub calls **us**. HMAC over the body is the only mechanism
GitHub uses to authenticate webhooks.

### 3.3 Installation-time security — already handled by GitHub

The GitHub App installation flow itself is hardened by GitHub:

- App installation is initiated by an authenticated owner/admin on the
  target repo.
- Permission scopes are declared in the App manifest and shown to the
  installer for explicit approval.
- Suspension or uninstall takes effect via the App's installation
  record; webhook delivery from a removed installation is reclaimed by
  GitHub.

Nothing on our side can harden that initial installation — it is gated
by GitHub's identity, scope review, and install-permissions model.

### 3.4 What CAN be hardened on the receiver

Real defense-in-depth improvements live in the receiver, not the App
manifest. Four candidates, ranked by cost/benefit:

| # | Mechanism | Value | Cost | Verdict |
|---|-----------|-------|------|---------|
| 1 | **Replay-prevention cache** on `X-GitHub-Delivery` | High — mitigates intercepted signed body re-post | Low — in-memory TTL cache | **Recommended.** |
| 2 | **IP allowlist** from `https://api.github.com/meta` (`hooks`) | Medium — drops spoofed sources before HMAC check | Medium — periodic refresh; GitHub IPs change | Optional. Use if external attacker probing cost is a concern. |
| 3 | **Installation-ID liveness check** via signed JWT + `GET /app/installations/{id}` | Medium — catches "leaked secret + uninstalled App" case | High — App private key in container; cache lifetimes; external API call per delivery | Optional. Use only if `OS_APP_PRIVATE_KEY` is acceptable to ship in the receiver. |
| 4 | **Freshness window** on payload `created_at` (e.g. `<= 5 min`) | Low — narrow replay protection | Low — gateway holds a clock | Optional. Redundant with #1. |

## 4. Recommendation

HMAC-SHA256 signature verification is the correct primary mechanism and
should remain. Add **only #1 (replay prevention)** in this iteration:

- Cache `X-GitHub-Delivery` for a configurable TTL (default 10 minutes).
- On match, reject as a replay with `422 Unprocessable Entity`.
- Cache state (in-memory with `cachetools.TTLCache`, no Redis dependency
  required) so a single receiver restart does not invalidate the cache
  (acceptable trade-off — short TTL means minimal exposure window after
  restart).

Defer #2-#4 unless a documented threat requires them.

## 5. Acceptance Criteria (proposed, only if approved)

- [ ] `webhook_receiver/security.py` (new) exports `ReplayCache` with
      `check_and_record(delivery_id) -> bool`.
- [ ] `/webhooks/github` rejects duplicate `X-GitHub-Delivery` within
      `WEBHOOK_REPLAY_TTL_SECONDS` (default 600) with HTTP 422.
- [ ] Cache is in-memory only; no persistence across restarts. Cmd-line
      flag and `WEBHOOK_REPLAY_TTL_SECONDS` env var control behavior.
- [ ] Python tests cover: unique IDs pass; duplicate ID within TTL
      rejected; TTL expiry re-admits; missing `X-GitHub-Delivery` still
      reaches the signature gate unchanged.
- [ ] Test coverage stays `> 85%`.

## 6. Validation Plan

1. `pwsh -NoProfile -File ./scripts/validate.ps1 -All`
2. Manual: send the same signed body twice with the same
   `X-GitHub-Delivery`; second request must be rejected.
3. Manual: send a body with a forged `X-GitHub-Delivery` but the real
   secret → second request rejected **before** signature check is fine
   (faster rejection), but signature must still gate first-time IDs.

## 7. Phased Plan

- **Phase 1 (this iteration):** Replay-prevention cache only.
- **Phase 2 (deferred):** IP allowlist if needed.
- **Phase 3 (deferred):** Installation-ID liveness check, gated on a
  decision to ship `OS_APP_PRIVATE_KEY` in the receiver.

## 8. Out of Scope

- Hardening GitHub App installation itself (owned by GitHub).
- Moving HMAC verification upstream to Caddy (defense in depth but
  duplicates the FastAPI verify; not worth the configuration drift).
- Rotating `OS_WEBHOOK_SECRET` automatically (GitHub's "Redeliver"
  re-signs with the current secret, so manual rotation is sufficient
  and surprises are minimized).
