"""Shared parser for the opencode client tool-stream (captured ``.stderr``).

The opencode client prints each tool/stream event as a leading "glyph" line
(``•`` task, ``✓`` done, ``✗`` error, ``⚙`` tool, ``%`` WebFetch, ``→`` Read,
``←`` Write, ``✱`` Glob, ``#`` Todos), optionally wrapped in ANSI escape codes.
Both :mod:`webhook_receiver.runner` (run-completion / zero-work classification)
and :mod:`webhook_receiver.dashboard` (the live ``/dashboard/events`` feed)
decode these same bytes, so the ANSI stripping and glyph detection live here as
the single source — preventing the two consumers from drifting on stream-format
changes (which would silently make run classification disagree with the feed).
"""
from __future__ import annotations

import re
from typing import Any

# All CSI ANSI sequences (SGR, cursor moves, erase, …). Broader than SGR-only so
# a stray clear-line (``\x1b[2K``) next to a glyph is stripped rather than left
# to break the leading-glyph match.
ANSI_RE = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")

# A glyph is the leading non-alphanumeric, non-punctuation marker opencode emits
# before each stream event. Permissive by design: a future glyph (e.g. opencode
# switching ``⚙`` → ``🛠``) still matches so classification does not silently
# break. Mirrors the original runner char-class.
_GLYPH_CHAR = re.compile(r"^[^A-Za-z0-9 \t\r\n{}()\[\]\"'<>,;:|\\/]")

_TOKEN_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*)")

# Glyph → event kind for the dashboard feed. Unknown glyphs are handled by the
# non-glyph branches below (model/watchdog/error/exit) or dropped, but a known
# glyph that changes meaning only needs updating here once.
_GLYPH_KIND: dict[str, str] = {
    "•": "delegation",
    "✓": "delegation_done",
    "✗": "error",
    "⚙": "tool",
    "%": "webfetch",
    "→": "read",
    "←": "write",
    "✱": "glob",
    "#": "checklist",
}

_AGENT_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9-]*)\s+Agent\s*$")

# Lines that carry no diagnostic value (server boot / migration noise); skipped
# by the event feed so it stays readable.
_NOISE_PREFIXES = (
    "INFO ",
    "sqlite-migration",
    "Database migration",
    "Performing one time",
)


def _stripped(line: str) -> str:
    """Return *line* with all ANSI escapes removed and whitespace trimmed."""
    return ANSI_RE.sub("", line).strip()


def extract_tool_names(stderr_text: str) -> set[str]:
    """Lowercased set of the leading stream token on each glyph line.

    Used by :mod:`webhook_receiver.runner` to classify a clean-exit run as
    zero-work vs real work. One token per glyph-leading line (e.g. ``⚙ bash``
    → ``bash``, ``→ Read`` → ``read``, ``• Execute …`` → ``execute``), matching
    the original permissive glyph detection so behavior is unchanged.
    """
    tools: set[str] = set()
    for raw in stderr_text.splitlines():
        line = _stripped(raw)
        if not line or not _GLYPH_CHAR.match(line):
            continue
        m = _TOKEN_RE.match(line[1:].lstrip())
        if m:
            tools.add(m.group(1).lower())
    return tools


def parse_events(stderr_text: str) -> list[dict[str, Any]]:
    """Typed event list ``[{seq, kind, agent, detail}, …]`` for the live feed.

    Keeps high-signal glyphs (delegations, tool calls, errors, watchdog, the
    model marker, the checklist) and drops framework boot noise.
    """
    events: list[dict[str, Any]] = []
    seq = 0
    for raw in stderr_text.splitlines():
        line = _stripped(raw)
        if not line or line.startswith(_NOISE_PREFIXES):
            continue
        glyph = line[0]
        if glyph in _GLYPH_KIND:
            kind = _GLYPH_KIND[glyph]
            detail = line[1:].strip()
        elif line.startswith("> orchestrator"):
            kind, detail = "model", line
        elif "[watchdog]" in line:
            kind, detail = "watchdog", line
        elif "Invalid Tool" in line or "Rate limit" in line:
            kind, detail = "error", line
        elif "exited with code" in line.lower():
            kind, detail = "exit", line
        else:
            continue  # unmapped line — drop to keep the feed clean
        agent: str | None = None
        if kind in ("delegation", "delegation_done"):
            m = _AGENT_RE.search(detail)
            if m:
                agent = m.group(1)
                detail = detail[: m.start()].rstrip()
        if kind == "tool" and len(detail) > 140:
            detail = detail[:140].rstrip() + "…"
        seq += 1
        events.append({"seq": seq, "kind": kind, "agent": agent, "detail": detail})
    return events
