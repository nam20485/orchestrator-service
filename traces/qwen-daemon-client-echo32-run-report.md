# Run Report — qwen-daemon-client-echo32 (Dispatch Never Delivered)

**Repo:** `nam20485/qwen-daemon-client-echo32`
**Run ID:** none — no run was ever dispatched
**Session:** none — no opencode session was ever created
**Trigger (intended):** `issues / labeled` — label `gh-issue-tracking:direct-body` on issue #1 (body `/gh-issue-tracking-init`), filed by `create-dispatch-issue.ps1` as `nam20485`
**Window:** 2026-09-05 13:22:38 (stack up) -> present; trigger window 13:37:40–13:37:56 UTC
**Outcome:** **NO RUN — the dispatch webhook delivery from GitHub never arrived at the receiver.** The receiver, Caddy, funnel, and opencode server were all healthy and idle the entire window. This is not a watchdog/permission deadlock; nothing reached the orchestrator at all.

---

## TL;DR — One failure mode, fatal at the gate

| # | Failure | When (UTC) | Recovered? | Fatal? |
|---|---------|------------|------------|--------|
| 1 | **`issues.opened` / `issues.labeled` webhook deliveries never arrived from GitHub** (App event subscription/permission gap suspected) | 13:37:55–13:37:56 (expected); nothing received | No — no delivery, no retry | **YES** (run never started) |

GitHub's own issue timeline proves the trigger fired (issue created 13:37:55Z, `labeled` event 13:37:56Z by `nam20485`), and the transport path was demonstrably working — five `label.created` deliveries from the same App, same repo, arrived successfully 3–15 seconds earlier (13:37:41–13:37:52). The receiver filtered those correctly and then sat idle. The single missing link is GitHub-side: the App never delivered `issues` events. The receiver's filtering, HMAC validation, and direct-body sender gating were never even exercised.

---

## Sessions

| Role | Session ID | Model | Steps reached |
|------|-----------|-------|---------------|
| — | — | — | none (no session created) |

`orchestratorservice` log for the window contains no `message="creating instance"` and no `message=created id=ses_...` lines — confirmed by the merged key-event grep over the last 60 minutes.

---

## Definitive root cause (with evidence)

### What the receiver actually received (container log, `docker compose logs webhook-receiver`)

```
2026-09-05 13:37:40,387 INFO Webhook received delivery_id=f6a84a50-… event=installation_repositories action=added repo=? sender=nam20485
2026-09-05 13:37:40,388 INFO Filtered … reason=event 'installation_repositories' not dispatched (only issues)
2026-09-05 13:37:41,330 INFO Webhook received delivery_id=f6fb2540-… event=label action=created repo=nam20485/qwen-daemon-client-echo32 sender=ghost
2026-09-05 13:37:48,090 INFO Webhook received delivery_id=fb239940-… event=label action=created … sender=nam20485
2026-09-05 13:37:48,418 INFO Webhook received delivery_id=fb57efb0-… event=label action=created … sender=nam20485
2026-09-05 13:37:51,504 INFO Webhook received delivery_id=fd2cafb0-… event=label action=created … sender=nam20485
2026-09-05 13:37:52,225 INFO Webhook received delivery_id=fd886440-… event=label action=created … sender=nam20485
```

All six returned `202 Accepted` and were filtered for the correct reasons (`event 'installation_repositories'/'label' not dispatched (only issues)`). **No POST of any kind hits the receiver after 13:37:52** — not even a signature-rejected one.

### What GitHub says happened next (`gh issue view` + `gh api …/issues/1/timeline`)

```
createdAt: 2026-09-05T13:37:55Z   body: "/gh-issue-tracking-init"
labels: [gh-issue-tracking:direct-body]
{"actor":"nam20485","created_at":"2026-09-05T13:37:56Z","event":"labeled","label":"gh-issue-tracking:direct-body"}
```

So GitHub generated the `labeled` event 4 seconds after the last successful delivery — and it (plus the `issues.opened` event) never reached the host. No HTTP status, no 401, nothing: the request was never delivered.

### Why the cause is GitHub-side, not local

1. **Transport proven healthy in-window:** the `label` deliveries at 13:37:48–:52 traversed funnel → Caddy (`172.24.0.4`) → receiver successfully, 3–7 seconds before the missing events.
2. **Receiver up and healthy the whole time:** stack restarted 13:22:38; `GET /health` 200s run continuously through the window (receiver log).
3. **No repo-level webhook exists** — `gh api repos/nam20485/qwen-daemon-client-echo32/hooks` → `[]`. Delivery depends entirely on the GitHub App.
4. **House requirement:** `AGENTS.md`: "Subscribing to `issues` requires App **Issues: Read** (read is enough for webhook delivery)." A GitHub App that lacks the `issues` event subscription (or Issues: Read permission) will deliver `label` events but silently never send `issues` events — exactly the observed signature. (The repo was added to the installation at 13:37:40; `installation_repositories` and `label` events flow on metadata-level permissions.)

**Definitive root cause:** the dispatch trigger was never delivered because the GitHub App did not send the `issues` webhook events — with `label` events flowing and zero requests arriving (not even failed ones), the upstream cause is the App's missing `issues` event subscription / Issues:Read permission, not anything in orchestrator-service. The receiver behaved correctly throughout. There is no watchdog angle: no session, no watchdog armed, nothing to abort.

### Assumption (labeled)

The App's webhook delivery log (GitHub App settings → Advanced → Recent Deliveries) was NOT inspectable from this machine: `gh api /app-hook/deliveries` returns 404 with a user PAT (it requires App JWT auth). If that log instead shows *red failed deliveries* for `issues` at 13:37:55–56, the cause shifts to a transient funnel/transport failure in that 3-second window — considered unlikely given the 13:37:52 success, but it is the one check that fully closes the loop.

---

## Solutions (options, recommendation, why)

**(A) Fix the App's event subscription, then re-trigger.** In GitHub Settings → Developer settings → the orchestrator GitHub App: set Permissions → Issues → **Read-only**, and under "Subscribe to events" check **Issues**. Then re-arm the trigger without re-filing the issue:
```
gh issue edit 1 --repo nam20485/qwen-daemon-client-echo32 --remove-label "gh-issue-tracking:direct-body"
gh issue edit 1 --repo nam20485/qwen-daemon-client-echo32 --add-label    "gh-issue-tracking:direct-body"
```
Each re-add fires a fresh `issues.labeled` delivery (sender `nam20485`, who must be in `DIRECT_BODY_ALLOWED_SENDERS` — receiver-side, already configured).

**(B) If the App's Recent Deliveries shows red/failed `issues` deliveries:** open one and inspect the response (DNS/TLS/502 from the funnel) — fix the funnel/Caddy path and use the "Redeliver" button; no re-trigger needed.

**(C) Hardening (optional, future):** an outward-facing liveness gap — the factory has no alert when a filed dispatch issue produces no webhook within N minutes. A periodic `gh issue list` sweep in `beads_loop` could flag "dispatch issue with direct-body label but no corresponding run" as a dashboard event. Not required to resolve this incident.

**RECOMMENDATION: (A).** The evidence signature (label events delivered, issues events absent without any failed request) matches an App event-subscription/permission gap exactly, and (A) both fixes and re-verifies in under two minutes. Confirm in the App's Recent Deliveries page while there — absence of `issues` entries there is the definitive proof; presence of red entries redirects to (B).

---

## Timeline (annotated)

| Time (UTC) | Event |
|------------|-------|
| 13:22:38 | Stack (re)started; `webhook-receiver` uvicorn up on :8080; BeadsLoop started; health checks green |
| 13:37:40 | `installation_repositories action=added` delivered (repo added to the App installation) — filtered correctly |
| 13:37:41 | `label action=created` sender=`ghost` (repo-default labels at repo creation) — delivered, filtered |
| 13:37:48–:52 | 4× `label action=created` sender=`nam20485` (label import via PAT) — delivered (202), filtered correctly |
| 13:37:55 | Issue #1 created via `gh issue create --label gh-issue-tracking:direct-body` — **`issues.opened` delivery never received** |
| 13:37:56 | GitHub records the `labeled` event (actor `nam20485`) — **`issues.labeled` delivery never received** |
| 13:37:56→now | Receiver idle (health checks only); no dispatch, no session, watchdog never armed; `orchestratorservice` log silent (no `creating instance` / `created id=ses_…`) |

---

## Run progress vs completion state

**What worked (receiver-side, all correct):**
- Funnel → Caddy → receiver transport live and verified by 6 successful deliveries in-window
- Event/action filtering correct for every non-issues event (`only issues`)
- All non-matching deliveries acknowledged 202 (GitHub will not retry; nothing lost)
- Stack healthy (compose ps: all three services healthy)

**Deliverables (dispatch contract) — none reached:**
- Dispatch matched: 0/1 — the trigger never arrived
- Session/orchestration: 0/1 — never started
- `/gh-issue-tracking-init` outcome (issue hierarchy/project/milestones): 0/1
- Issue #1 remains OPEN with the `gh-issue-tracking:direct-body` label and no orchestrator comments

---

## Evidence sources

- `docker compose -f compose.yaml ps` — all services healthy (stack up 18 min at inspection)
- `docker compose -f compose.yaml logs --since=60m orchestratorservice webhook-receiver | grep -iE "Webhook received|creating instance|created id=|loop session|asking id=|PERMISSION DEADLOCK|abort|publish|closed|qwen-daemon"` — the complete in-window delivery record; no session lines
- `docker compose -f compose.yaml logs --since=60m webhook-receiver` (full tail) — filter reasons + 202s + health checks; nothing after 13:37:52
- `docker compose -f compose.yaml logs --since=60m webhook-proxy` — Caddy admin-api noise only (site access logging not enabled); no POSTs logged, receiver log is authoritative
- `gh issue view 1 --repo nam20485/qwen-daemon-client-echo32 --json …` — issue body, label, createdAt
- `gh api repos/nam20485/qwen-daemon-client-echo32/issues/1/timeline` — `labeled` event at 13:37:56Z by nam20485
- `gh api repos/nam20485/qwen-daemon-client-echo32/hooks` — `[]` (no repo webhook; App-only delivery)
- `gh api /app-hook/deliveries` — 404 with user PAT (App delivery log not readable from here)
- `AGENTS.md` (webhook stack notes): "Subscribing to `issues` requires App Issues: Read"
- `/home/nam20485/src/github/nam20485/workflow-launch2/scripts/create-dispatch-issue.ps1` — trigger files the issue with `--label` in one `gh issue create` call
