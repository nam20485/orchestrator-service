from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from webhook_receiver.run_stream import parse_events

# ── Completion-state detection ──────────────────────────────────────────────
# Maps the runner's manifest ``classification`` field to a human-readable
# completion state for the narrative view. The runner already classifies every
# exit path (completed / failed / zero_work / incomplete / idle_timeout /
# hard_ceiling / consecutive_errors); see runner.py ``_run_completion_watcher``.
_CLASSIFICATION_STATUS: dict[str, str] = {
    "completed": "completed",
    "failed": "failed",
    "zero_work": "zero_work",
    "incomplete": "incomplete",
    "idle_timeout": "timeout",
    "hard_ceiling": "timeout",
    "consecutive_errors": "error",
}

# Watchdog kill-reason → human-readable exit message prefix.
_KILL_REASON_MESSAGE: dict[str, str] = {
    "idle_timeout": "Watchdog killed: agent went idle (no output)",
    "hard_ceiling": "Watchdog killed: hit the hard runtime ceiling",
    "consecutive_errors": "Watchdog killed: consecutive errors exceeded threshold",
    "process_exit": "Process exited",
}

# Exit-code → human readable note for non-watchdog failures.
_EXIT_RE = re.compile(r"exited with code (\d+)", re.IGNORECASE)


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without trailing ``Z``)."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_s(started_at: str | None, ended_at: str | None) -> float | None:
    """Return the run duration in seconds, or ``None`` when times are missing."""
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None:
        return None
    if end is None:
        return None
    delta = end - start
    return round(delta.total_seconds(), 1)


def _determine_status(manifest: dict) -> tuple[str, str]:
    """Derive (status, exit_message) from the run manifest.

    The runner writes ``classification`` and ``exit_code`` to the manifest on
    completion. When the manifest has no ``ended_at`` the run is still active.
    """
    if not manifest.get("ended_at"):
        return "running", "Run in progress…"

    exit_code = manifest.get("exit_code")
    classification = manifest.get("classification", "")
    kill_reason = manifest.get("kill_reason")
    timed_out = manifest.get("timed_out", False)

    status = _CLASSIFICATION_STATUS.get(classification, "unknown")

    if kill_reason:
        msg = _KILL_REASON_MESSAGE.get(kill_reason, f"Killed ({kill_reason})")
    elif classification == "completed":
        msg = "Completed successfully"
    elif classification == "zero_work":
        msg = "Exited cleanly but no execution tools were used (planning only)"
    elif classification == "incomplete":
        msg = "Exited cleanly but the dispatch issue is still open (incomplete)"
    elif classification == "failed":
        msg = f"Failed (exit code {exit_code})"
    elif timed_out:
        msg = "Timed out"
    else:
        msg = f"Exited (code {exit_code})"

    return status, msg


# ── Timeline synthesis ─────────────────────────────────────────────────────

# Event kinds that represent file reads / globs / web fetches. Consecutive
# entries of these types are collapsed into a single grouped summary so the
# timeline stays readable (5 dozen ``→ Read …`` lines → one summary).
_GROUPABLE: set[str] = {"read", "glob", "webfetch"}


def _build_timeline(
    events: list[dict[str, Any]],
    started_at: str | None,
    duration_s: float | None,
) -> list[dict[str, Any]]:
    """Convert parsed glyph events into a narrative timeline.

    When timing is available (``duration_s``) each entry gets an estimated
    ``offset_s`` (seconds from run start) proportional to its position. Without
    timing, entries carry only their sequence index.
    """
    n = len(events)
    total = duration_s or 0
    timeline: list[dict[str, Any]] = []
    for ev in events:
        offset = round(total * (ev["seq"] - 1) / n, 1) if n > 1 and total else 0
        kind = ev["kind"]
        detail = ev.get("detail", "")
        agent = ev.get("agent")
        entry: dict[str, Any] = {
            "seq": ev["seq"],
            "kind": kind,
            "agent": agent,
            "detail": detail,
            "offset_s": offset,
        }
        entry["summary"] = _summarize_event(kind, agent, detail)
        timeline.append(entry)
    return timeline


def _summarize_event(kind: str, agent: str | None, detail: str) -> str:
    """One-line human-readable summary for a single timeline event."""
    if kind == "delegation":
        return f"Delegated to {agent or 'subagent'} agent" + (
            f": {detail}" if detail else ""
        )
    if kind == "delegation_done":
        return f"Subagent {agent or ''} returned".strip()
    if kind == "error":
        return f"Error: {detail}"
    if kind == "exit":
        return detail
    if kind == "watchdog":
        return f"Watchdog: {detail}"
    if kind == "model":
        return f"Model: {detail}"
    if kind == "checklist":
        return f"Checklist: {detail}"
    if kind == "read":
        # Extract just the filename from a full path.
        if "/" in detail:
            detail = detail.rsplit("/", 1)[-1]
        return f"Read {detail}"
    if kind == "glob":
        return f"Search files: {detail}"
    if kind == "webfetch":
        return f"Fetch URL: {detail}"
    if kind == "write":
        return f"Write {detail}"
    if kind == "tool":
        return detail
    return detail


def _group_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse consecutive groupable events into summary entries.

    A run may emit dozens of ``→ Read`` / ``✱ Glob`` lines in a row. Collapsing
    them into a single ``"Read 15 files"`` keeps the narrative scannable while
    preserving non-groupable events (delegations, errors, watchdog, exit) as
    individual high-signal lines.
    """
    if not timeline:
        return []

    grouped: list[dict[str, Any]] = []
    i = 0
    while i < len(timeline):
        entry = timeline[i]
        if entry["kind"] not in _GROUPABLE:
            grouped.append(entry)
            i += 1
            continue

        # Collect the run of consecutive groupable entries of the *same* kind.
        run = [entry]
        j = i + 1
        while j < len(timeline) and timeline[j]["kind"] == entry["kind"]:
            run.append(timeline[j])
            j += 1

        if len(run) == 1:
            grouped.append(entry)
        else:
            # Synthesize a grouped summary: keep the first offset, list the
            # count, and stash the individual details in ``items`` for
            # expandable detail in the UI.
            kind = entry["kind"]
            labels = {"read": "files", "glob": "patterns", "webfetch": "URLs"}
            noun = labels.get(kind, "items")
            grouped.append(
                {
                    "seq": entry["seq"],
                    "kind": kind,
                    "agent": None,
                    "detail": f"{len(run)} {noun}",
                    "offset_s": entry["offset_s"],
                    "summary": f"{_verb_for_kind(kind)} {len(run)} {noun}",
                    "items": [r["detail"] for r in run],
                }
            )
        i = j
    return grouped


def _verb_for_kind(kind: str) -> str:
    return {"read": "Read", "glob": "Searched", "webfetch": "Fetched"}.get(
        kind, "Processed"
    )


# ── Stats ──────────────────────────────────────────────────────────────────


def _build_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-kind counts from the raw parsed events (pre-grouping)."""
    counts: dict[str, int] = {}
    for ev in events:
        k = ev["kind"]
        counts[k] = counts.get(k, 0) + 1
    return {
        "tool_calls": counts.get("tool", 0),
        "delegations": counts.get("delegation", 0),
        "errors": counts.get("error", 0),
        "exits": counts.get("exit", 0),
        "watchdog": counts.get("watchdog", 0),
        "files_read": counts.get("read", 0),
        "files_written": counts.get("write", 0),
        "globs": counts.get("glob", 0),
        "webfetches": counts.get("webfetch", 0),
        "checklists": counts.get("checklist", 0),
        "model_markers": counts.get("model", 0),
        "total_events": len(events),
    }


# ── Public API ────────────────────────────────────────────────────────────


def parse_narrative(
    stderr: str,
    stdout: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Synthesize a human-readable narrative from a run's captured logs.

    Combines the structured glyph events (parsed from ``.stderr``) with the
    manifest's lifecycle metadata to produce:

    * ``summary`` — completion state, duration, exit message
    * ``timeline`` — grouped, summarized event entries with approximate timing
    * ``stats`` — per-kind event counts

    *stdout* is currently unused but accepted for future correlation (e.g.
    detecting a final status line the agent wrote to stdout rather than stderr).
    """
    events = parse_events(stderr)

    dur = _duration_s(
        manifest.get("started_at"),
        manifest.get("ended_at"),
    )
    status, exit_message = _determine_status(manifest)

    raw_timeline = _build_timeline(events, manifest.get("started_at"), dur)
    timeline = _group_timeline(raw_timeline)
    stats = _build_stats(events)

    return {
        "summary": {
            "status": status,
            "duration_s": dur,
            "started_at": manifest.get("started_at"),
            "ended_at": manifest.get("ended_at"),
            "exit_code": manifest.get("exit_code"),
            "exit_message": exit_message,
            "classification": manifest.get("classification"),
            "tools": manifest.get("tools", []),
        },
        "timeline": timeline,
        "stats": stats,
    }