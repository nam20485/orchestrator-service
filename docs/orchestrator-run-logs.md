# Monitoring Orchestrator Runs (Logs)

How to find, read, and triage webhook-dispatched orchestrator runs. The receiver
(`webhook_receiver/runner.py`) captures every dispatch's prompt, stdout, and stderr plus a
manifest sidecar, so a run can be diagnosed without GitHub access.

## Where logs live

| Where | Path | Notes |
|-------|------|-------|
| Host (persisted) | `${WEBHOOK_LOG_DIR:-./traces/runner}/` | Compose bind-mounts this onto the container path. Survives container restarts. Gitignored (`traces/runner/`). |
| Container | `/tmp/orchestrator-webhook/` | The in-container path `runner.py`/`beads_loop.py`/`dashboard.py` write to (= `Settings.log_dir`). |

Each dispatch produces four files keyed by a slug `<owner>__<repo>__issue-<n>__<workflow>__<UTCts>-<rand>`:

- `<slug>.md` — the exact prompt sent to the orchestrator.
- `<slug>.stdout` — the orchestrator's text output (its narrative / final answer).
- `<slug>.stderr` — the opencode client tool stream (the `--print-logs` glyphs).
- `<slug>.manifest.json` — identity + lifecycle metadata (repo, issue, workflow, pid, started_at, ended_at, exit_code, **classification**, tools).

## Viewing logs

- **Dashboard** (token-gated, `DASHBOARD_TOKEN`): `/dashboard/runs` lists runs newest-first; `/dashboard/runs/<slug>` shows prompt / stdout / stderr tabs and the classification badge.
- **Shell (host):** `ls -t traces/runner/` then `less traces/runner/<slug>.stderr`. Find a run by identity:
  `ls traces/runner/ | grep 'owner__repo__issue-7'`.

## How to read a run log (the checklist signal)

A healthy `project-setup` run **re-prints its TODO checklist after every assignment** with
`[ ]`→`[x]` transitions — this is the single most reliable health signal (see the golden-path
anchors in `traces/golden-path-foxtrot54-project-setup.md`). The `.stderr` tool stream prefixes
each tool call with a glyph:

| Glyph | Meaning |
|-------|---------|
| `⚙` | tool call (e.g. `⚙ sequential-thinking_sequentialthinking`, `⚙ memory_search_nodes`) |
| `%` | `WebFetch` |
| `→` | `Read` |
| `←` | `Write` |
| `•` | `task` (subagent delegation) |
| `✱` | `Glob` |
| `#` | `Todos` (the checklist!) |

Read order: skim `.stdout` for the agent's stated plan, then `.stderr` for what it actually did.
If `.stderr` ends mid-workflow and the checklist has unchecked items, the run stalled.

## Run classification

`runner.py::_run_completion_watcher` classifies each completed run (recorded in the manifest's
`classification` field and emitted as an event):

| Classification | Trigger | Issue comment? |
|----------------|---------|----------------|
| `completed` | exit 0, real tools used, **dispatch issue closed** | no |
| `incomplete` | exit 0, real tools used, but **dispatch issue still open** (the workflow's own success contract wasn't met) | ⚠️ advisory |
| `zero_work` | exit 0 but **only planning/reading tools** (narrate-and-self-terminate) | ⚠️ advisory |
| `failed` | non-zero exit / killed / timeout | ❌ failure |

The `incomplete` class is exactly the gap-miner failure mode: exit 0, real work done, but the
workflow was abandoned partway and the dispatch issue stayed open.

## Catching hung runs

`DISPATCH_TIMEOUT_SECS` (env, default unset = wait forever) kills a run that exceeds the budget and
flags it `failed`. Recommended value ≈ `5400` (the golden-path `project-setup` runtime) so a stuck
run is surfaced instead of hanging silently.

## Example triage (gap-miner-v2-delta48)

The dispatch `prompt-373i6gxj` (preserved at `traces/gap-miner-v2-delta48-373i6gxj.*`) shows the
orchestrator loaded the full `project-setup` workflow, built the correct checklist, then — lacking
a `bash` tool — delegated the entire workflow to a single Planner subagent and never resumed. The
`.stderr` ends at `• Execute project-setup workflow  Planner Agent`; the dispatch issue stayed open.
Full account: `traces/gap-miner-v2-delta48-execution-flow.md`.
