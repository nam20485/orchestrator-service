# Data models

`orchestrator-service` keeps three kinds of run-time data: records **persisted to disk** under the receiver's log directory, structures that live **only in process memory** (lost on restart), and structures **synthesized on read** from the other two. This page documents the shape of each, sourced directly from the modules that create them.

## Source files

| Path | What it contributes |
| --- | --- |
| `webhook_receiver/config.py` | The `Settings` dataclass — the configuration object every other model is built from (`log_dir`, watchdog tunables, dispatch parameters). |
| `webhook_receiver/event_store.py` | `EventStore`, `Subscriber` — the in-memory event ring buffer and its SSE fan-out. |
| `webhook_receiver/webhook_store.py` | `WebhookStore` — the persisted, JSON-array-backed webhook delivery record. |
| `webhook_receiver/runner.py` | `DispatchContext`, and the run manifest sidecar writers (`_write_run_manifest`/`_update_run_manifest`). |
| `webhook_receiver/watchdog.py` | `WatchdogState`, `WatchdogSnapshot`, `WatchdogConfig`, `WatchdogResult`, and the kill-reason constants. |
| `webhook_receiver/run_narrative.py` | `parse_narrative()` — the derived summary/timeline/stats structure computed from a manifest + captured stderr. |
| `docs/openapi.json` | The exported FastAPI schema — shows which of these structures are typed vs. passed through as opaque JSON at the API boundary. |

## Persisted records

### Run manifest (`<stem>.manifest.json`)

Written by `webhook_receiver/runner.py` to `{log_dir}/<stem>.manifest.json` (`log_dir` defaults to `{tempfile.gettempdir()}/orchestrator-webhook`, bind-mounted by Compose to `${WEBHOOK_LOG_DIR:-./traces/runner}`). Written in two phases: an initial dispatch-time write (`_write_run_manifest`), then a completion-time merge (`_update_run_manifest`) that reads the existing file, updates it, and rewrites it whole.

Dispatch-time fields (written by `dispatch_to_opencode`):

| Field | Type | Description |
| --- | --- | --- |
| `stem` | string | The manifest's own filename stem — the dispatch identity. |
| `repo_full_name` | string \| null | `owner/repo` of the triggering issue, from `DispatchContext`. |
| `issue_number` | int \| null | Triggering issue number. |
| `html_url` | string \| null | Link to the triggering issue. |
| `workflow` | string \| null | Parsed from `$workflow_name = X` in the prompt body, if present. |
| `prompt_file` | string | Filename of the rendered prompt (`<stem>.md`). |
| `pid` | int | PID of the spawned `pwsh` process. |
| `started_at` | string | UTC timestamp, `%Y%m%dT%H%M%SZ` format. |
| `model` | string | `Settings.model` used for this dispatch. |
| `agent` | string | `Settings.agent` used for this dispatch. |
| `log_dir` | string | Absolute path to the log directory. |

Completion-time fields merged in by `_run_completion_watcher`:

| Field | Type | Description |
| --- | --- | --- |
| `ended_at` | string | ISO-8601 UTC timestamp. |
| `exit_code` | int | Process exit code (or the watchdog's synthesized code on kill). |
| `timed_out` | bool | `true` for `idle_timeout`/`hard_ceiling` kills. |
| `kill_reason` | string \| null | One of the watchdog reason constants (below), or `null` for a self-exit. |
| `classification` | string | One of `completed`, `failed`, `zero_work`, `incomplete`, or a watchdog reason. |
| `consecutive_errors` | int | Consecutive error-line count at time of kill (0 if not killed for errors). |
| `tools` | list[string] | Sorted set of tool names parsed from the run's stderr. |

`classification` values map to a human-readable status (`run_narrative._CLASSIFICATION_STATUS`): `completed`→`completed`, `failed`→`failed`, `zero_work`→`zero_work`, `incomplete`→`incomplete`, `idle_timeout`/`hard_ceiling`→`timeout`, `consecutive_errors`/`permission_deadlock`→`error`.

### Webhook store record (`webhooks.json`)

`WebhookStore` (`webhook_receiver/webhook_store.py`) persists one JSON array to `{log_dir}/webhooks.json`, keyed in memory by `delivery_id`. Chosen over JSON-lines because entries need in-place mutation across phases (an append-only log would need collapse-on-read). Loaded fully into memory on startup; every mutation rewrites the whole file via a temp-file-then-`replace` pattern.

| Field | Type | Description |
| --- | --- | --- |
| `delivery_id` | string | Primary key — the GitHub delivery ID. |
| `received_ts` | float | Unix timestamp set on first `record()` call for this delivery. |
| `decision` | string | `pending` initially; updated to the dispatch decision (e.g. allowed/denied) by later `record()` calls. |
| `reason` | string *(optional)* | Free-form reason merged in by a later `record()` call. |
| `prompt_stem` | string *(optional)* | The run manifest stem this webhook resulted in, once dispatched — links a webhook to its run. |

Bounds: capped at `max_events` (default 1000, oldest evicted by `received_ts`); `cleanup_old()` additionally deletes entries older than `max_age_days` (default 30) on startup.

## In-memory-only records

These are never written to disk and are lost on process restart; `WebhookStore`/manifest files cover the durable observability path.

### `EventStore` events (`webhook_receiver/event_store.py`)

A thread-safe `deque(maxlen=1000)` with SSE fan-out to per-subscriber `queue.Queue(maxsize=500)` instances. Each event:

| Field | Type | Description |
| --- | --- | --- |
| `id` | int | Monotonically increasing counter (`itertools.count(1)`), unique per process lifetime. |
| `type` | string | Event type name, e.g. `dispatch_started`. |
| `ts` | float | `time.time()` at emission. |
| `data` | dict | Arbitrary `**kwargs` passed to `emit()`. |

Event types emitted by `webhook_receiver/runner.py`: `dispatch_started` (`prompt_file`, `pid`), `dispatch_completed` (`exit_code`, `prompt_file`), `dispatch_failed` (`exit_code`, `prompt_file`, `timed_out`), `dispatch_zero_work` (`exit_code`, `prompt_file`, `tools`), `dispatch_incomplete` (`exit_code`, `prompt_file`, `tools`). A `Subscriber` iterator yields `None` on a keepalive timeout (default 30s) rather than blocking indefinitely.

### `DispatchContext` (`webhook_receiver/runner.py`)

Frozen dataclass carrying the triggering webhook's identity into the completion watcher, so a failed/killed run can post a GitHub comment back on the originating issue.

| Field | Type | Description |
| --- | --- | --- |
| `repo_full_name` | string | `owner/repo`. |
| `issue_number` | int | Triggering issue number. |
| `html_url` | string \| null | Link to the issue. |
| `trigger_label` | string \| null | The label that matched dispatch (gates the "incomplete" check to specific labels). |

### Watchdog configuration and state

Defined in `webhook_receiver/watchdog.py`.

**`WatchdogConfig`** — frozen dataclass of tunables, built via `WatchdogConfig.from_settings(settings)` (reads the corresponding `Settings` attributes; see [Configuration](configuration.md#idle-watchdog)):

| Field | Default |
| --- | --- |
| `idle_timeout_secs` | `900` |
| `error_grace_secs` | `300` |
| `hard_ceiling_secs` | `5400` |
| `poll_interval_secs` | `30` |
| `max_consecutive_errors` | `5` |
| `sigterm_grace_secs` | `10` |
| `debug` | `False` |
| `permission_ask_grace_secs` | `60` |
| `server_log_path` | `/home/app/.local/share/opencode/log/opencode.log` |
| `server_url` | `""` (disables server-side session abort) |
| `server_username` | `"opencode"` |
| `server_password` | `""` |

**`WatchdogState`** — mutable, lock-protected, shared between the stdout/stderr reader threads and the watchdog poll loop. Every received line updates it via `record_line()`. Internal fields: `_start_time`, `_last_line_time`, `_consecutive_errors`, `_last_error_time`, `_last_error_message`, `_total_lines`.

**`WatchdogSnapshot`** — frozen, immutable point-in-time copy returned by `WatchdogState.snapshot()` so the poll loop reads a consistent view without holding the lock across I/O: `last_line_time`, `consecutive_errors`, `last_error_time`, `last_error_message`, `total_lines`.

**`WatchdogResult`** — frozen dataclass returned by `IdleWatchdog.run()` once the process exits or is killed:

| Field | Type | Description |
| --- | --- | --- |
| `killed` | bool | `True` if the watchdog terminated the process. |
| `reason` | string | One of the reason constants below. |
| `elapsed` | float | Seconds since dispatch start. |
| `exit_code` | int \| null | Process exit code. |
| `consecutive_errors` | int | Consecutive error count at exit/kill. |
| `last_error_message` | string | Truncated (200 chars) last error line. |
| `last_line_time` | float | Monotonic time of the last received line. |
| `total_lines` | int | Total stdout+stderr lines received. |

Reason constants: `REASON_PROCESS_EXIT` (`"process_exit"`, self-exit — not a kill), `REASON_IDLE_TIMEOUT`, `REASON_HARD_CEILING`, `REASON_CONSECUTIVE_ERRORS`, `REASON_PERMISSION_DEADLOCK` (an unanswered permission `ask` detected in the server log — unrecoverable in headless dispatch).

## Derived records (synthesized on read, never stored)

### Run narrative (`webhook_receiver/run_narrative.py`)

`parse_narrative(stderr, manifest)` combines a run's captured stderr (parsed into glyph events by `webhook_receiver.run_stream.parse_events`) with its manifest to build a dashboard-facing structure with no independent persistence — recomputed from the two source records above on every read:

```text
{
  "summary": {status, duration_s, started_at, ended_at, exit_code,
              exit_message, classification, tools},
  "timeline": [{seq, kind, agent, detail, offset_s, summary, items?}, ...],
  "stats": {tool_calls, delegations, errors, exits, watchdog, files_read,
            files_written, globs, webfetches, checklists, model_markers,
            total_events},
}
```

- `summary.status` comes from `_CLASSIFICATION_STATUS` (see above); `exit_message` is derived from `kill_reason` via `_KILL_REASON_MESSAGE`, or a classification-specific fallback message.
- `timeline` entries have `kind` ∈ `{delegation, delegation_done, error, exit, watchdog, model, checklist, read, glob, webfetch, write, tool}`. Consecutive `read`/`glob`/`webfetch` entries of the same kind are collapsed into a single grouped entry (`items` holds the individual details) so a run with dozens of file reads stays scannable.
- `stats` is a flat per-kind count over the *ungrouped* raw events.

## Generated API schemas (`docs/openapi.json`)

The exported FastAPI schema (produced by `scripts/export-openapi.py`) defines only two typed component schemas: `HTTPValidationError` and `ValidationError` — FastAPI's built-in 422 request-validation error shape. Every `/api/dashboard/*` JSON response is declared as `additionalProperties: true` (an untyped object or array of objects) — the actual response bodies are the Python dict/list structures documented above (the run manifest, `WebhookStore.list_events()` output, `EventStore.recent()` output, and `parse_narrative()`'s output), not a schema in `openapi.json`. This page, not the OpenAPI file, is the accurate reference for those response shapes; e.g. `GET /api/dashboard/runs/{stem}/narrative` returns exactly the `parse_narrative()` structure above.

## Related pages

- [Configuration](configuration.md) documents where each field's value comes from (env var, code default, or compose override).
- [Dependencies](dependencies.md) covers the CLIs (`gh`, `bvr`) and services these records support (issue comments, dashboard graph data).
- [Glossary](../overview/glossary.md) defines Manifest, EventStore, WebhookStore, and Watchdog at a narrative level.
