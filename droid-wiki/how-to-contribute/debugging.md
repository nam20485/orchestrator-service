# Debugging

Operational debugging for a running stack: is it up, what did a dispatched
run actually do, and why might it have been killed.

## Health

```bash
curl -s http://127.0.0.1:8081/health           # webhook-receiver, host loopback publish (Compose)
curl -s http://localhost:8080/health           # webhook-receiver, direct (local `uv run`)
curl -s http://localhost/health                # through the Caddy proxy on :80
```

All three should return `{"status":"ok"}`
(`README.md`,
`docs/deployment-compose.md`).

## Run logs

Every webhook-dispatched orchestration run is captured by
`webhook_receiver/runner.py` under a slug
`<owner>__<repo>__issue-<n>__<workflow>__<UTCts>-<rand>`, in four files
(`docs/orchestrator-run-logs.md`):

| File | Contents |
|------|----------|
| `<slug>.md` | The exact prompt sent to the orchestrator |
| `<slug>.stdout` | The orchestrator's narrative / final answer |
| `<slug>.stderr` | The opencode client tool stream (`--print-logs` glyphs) |
| `<slug>.manifest.json` | Identity + lifecycle metadata: repo, issue, workflow, pid, timestamps, exit code, classification, tools used |

Location: host path `${WEBHOOK_LOG_DIR:-./traces/runner}/` (bind-mounted into
the container at `/tmp/orchestrator-webhook/`), gitignored. List and inspect:

```bash
ls -t traces/runner/
less traces/runner/<slug>.stderr
ls traces/runner/ | grep 'owner__repo__issue-7'   # find a run by identity
```

`.stderr` glyphs (`docs/orchestrator-run-logs.md`):

| Glyph | Meaning |
|-------|---------|
| `⚙` | tool call |
| `%` | `WebFetch` |
| `→` | `Read` |
| `←` | `Write` |
| `•` | `task` (subagent delegation) |
| `✱` | `Glob` |
| `#` | `Todos` (the checklist) |

Read order: skim `.stdout` for the agent's stated plan, then `.stderr` for
what it actually did. A healthy `project-setup` run re-prints its `[ ]`→`[x]`
TODO checklist after every assignment — that reprint is the single most
reliable health signal. If `.stderr` ends mid-workflow with unchecked items,
the run stalled.

### Run classification

`runner.py::_run_completion_watcher` classifies every completed run into the
manifest's `classification` field:

| Classification | Trigger |
|-----------------|---------|
| `completed` | exit 0, real tools used, dispatch issue closed |
| `incomplete` | exit 0, real tools used, but the dispatch issue is still open (success contract not met) |
| `zero_work` | exit 0 but only planning/reading tools were used (narrate-and-self-terminate) |
| `failed` | non-zero exit / killed / timeout |

`incomplete` covers the specific failure mode where real work happened but
the workflow was abandoned partway and the dispatch issue never closed.

## Dashboard

Real-time UI for the Beads pipeline, served by `webhook-receiver`
(`docs/dashboard.md`):

| Environment | URL |
|-------------|-----|
| Compose (host) | `http://127.0.0.1:8081/dashboard` |
| Compose (another tailnet machine) | `https://<machine>.<tailnet>.ts.net:8443/dashboard` |
| Local `uv run orchestrator-webhook` | `http://localhost:8080/dashboard` |

`http://localhost/dashboard` — the Caddy site on host `:80` — returns `404` for
every dashboard path by design; that is the public webhook-only listener, not a
broken dashboard.

The dashboard is **disabled by default** — every route returns `404` until
`DASHBOARD_TOKEN` is set. Once set, authenticate via an
`Authorization: Bearer` header, `?token=` query parameter, or a
`dashboard_token` cookie:

```bash
export DASHBOARD_TOKEN="…"
xdg-open "http://127.0.0.1:8081/dashboard?token=$DASHBOARD_TOKEN"
curl -s -H "Authorization: Bearer $DASHBOARD_TOKEN" http://127.0.0.1:8081/api/dashboard/overview | jq
```

Key views: summary cards (Total/Ready/Blocked/Active/Closed/Halted), the
beads table (click a row to expand description + live stdout/stderr logs),
Active Agents, and a live Event Timeline over SSE. `/dashboard/runs` lists
dispatch runs; `/dashboard/runs/<slug>` shows the prompt/stdout/stderr tabs
and classification badge for one run — the same data described above under
"Run logs," surfaced without shell access. An empty beads table with "No
beads found. Run /plan-to-beads to create the DAG." means `.beads/` is not
yet initialized — a normal idle state, not an error.

## Watchdog conditions (why a run got killed)

Runs are monitored by the activity-aware idle watchdog
(`webhook_receiver/watchdog.py`). A run is killed and classified `failed` if
any of these trip (`docs/orchestrator-run-logs.md`):

| Condition | Default | Trigger |
|-----------|---------|---------|
| `IDLE_TIMEOUT_SECS` | 900s | No new stdout/stderr output for this long. Sustained OpenCode server-log growth (`OPENCODE_SERVER_LOG_PATH`) is also tracked as an activity signal, so a run that delegates to a subagent and goes silent on the client side does not get falsely killed while the server is still working. |
| `MAX_CONSECUTIVE_ERRORS` | 5 | That many error lines in a row with no intervening non-error line. |
| `HARD_CEILING_SECS` | 5400s | Absolute wall-clock ceiling regardless of activity (≈ a golden-path `project-setup` runtime). |

`DISPATCH_TIMEOUT_SECS` is a legacy knob: only used to feed the hard ceiling
if `HARD_CEILING_SECS` is unset. The kill uses an escalating process-group
termination so spawned children don't outlive the run.

## Normal states vs. real errors

When triaging, distinguish expected idle states from actual failures
(`AGENTS.md`, "Normal States vs. Errors"):

- `NOT_INITIALIZED` / "beads not initialized" and an empty `br ready --json` /
  `bvr --robot-next` result are **normal** — the service is "ready at will"
  and waits for `/plan-to-beads` to create the DAG.
- Non-`NOT_INITIALIZED` errors from `br`/`bvr` (e.g. `db locked`) **are**
  real errors, logged at `ERROR` level.
- A failed `br close` is retried up to `BEADS_MAX_RETRIES` (default 3); it
  does not crash the service on its own.

## Diagnosing CI failures

Never theorize about a CI failure. Fetch the real logs and quote the exact
failing line:

```bash
gh run list --workflow=validate.yml --limit 5
gh run view <run-id> --log-failed
```

## Container/compose sanity checks

```bash
docker compose -f compose.yaml config          # validate the merged compose model
docker compose logs -f webhook-receiver        # tail a service's logs
docker compose logs -f orchestratorservice
```

Remember `docker compose ... config` only validates the model against the
working tree — it says nothing about whether the *registry* image currently
deployed matches source (see [Testing](testing.md), Layer 3 limits).
