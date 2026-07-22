from __future__ import annotations

from webhook_receiver.run_stream import ANSI_RE, extract_tool_names, parse_events

# A compact synthetic stream mirroring the real opencode client format:
# ANSI-wrapped glyphs, the model marker, an error, a watchdog line, and boot
# noise. Deliberately includes a NON-SGR CSI (``\x1b[2K`` clear-line) before a
# glyph — the exact case that made the old runner regex (SGR-only) miss a tool
# call and false-classify a run as zero-work.
_SAMPLE = (
    "INFO  2026-07-05T02:47:02 service=default args=[...]\n"            # noise
    "sqlite-migration:done\n"                                            # noise
    "\x1b[0m> orchestrator \xc2\xb7 glm-5\x1b[0m\n"                      # model
    "\x1b[0m\u2022 \x1b[0mPost status update\x1b[90m Github-Expert Agent\x1b[0m\n"
    "\x1b[0m\u2713 \x1b[0mPost status update\x1b[90m Github-Expert Agent\x1b[0m\n"
    "\x1b[0m\u2699 \x1b[0mbash {\"command\":\"git status\"}\n"
    "\x1b[0m\u2192 \x1b[0mRead /workspace/repo/AGENTS.md\n"
    "\x1b[2K\u2699 memory_search_nodes {\"query\":\"repo\"}\n"           # non-SGR CSI prefix
    "\x1b[0m#\x1b[0m Todos\n"
    "\x1b[0m\u2717 \x1b[0mInvalid Tool unavailable 'memory-search_nodes'\n"
    "[watchdog] client output idle 87s, server I/O active\n"
)


def test_extract_tool_names_basic() -> None:
    tools = extract_tool_names(_SAMPLE)
    # one lowercased token per glyph line; boot noise excluded
    assert "bash" in tools
    assert "read" in tools
    assert "memory_search_nodes" in tools          # from the \x1b[2K line
    assert "execute" not in tools                   # "Post…" is not a token
    assert all(t == t.lower() for t in tools)


def test_parse_events_classifies_glyphs_and_drops_noise() -> None:
    events = parse_events(_SAMPLE)
    kinds = [e["kind"] for e in events]
    assert "model" in kinds
    assert "delegation" in kinds and "delegation_done" in kinds
    assert "tool" in kinds and "read" in kinds
    assert "checklist" in kinds
    assert "error" in kinds
    assert "watchdog" in kinds
    # boot noise excluded
    assert all(not e["detail"].startswith(("INFO ", "sqlite-migration")) for e in events)

    delegations = [e for e in events if e["kind"] == "delegation"]
    assert delegations and delegations[0]["agent"] == "Github-Expert"
    assert delegations[0]["detail"] == "Post status update"

    # long tool JSON is truncated
    long_tool = "\x1b[0m\u2699 \x1b[0mbash {\"command\":\"" + "x" * 200 + "\"}\n"
    tool_ev = next(e for e in parse_events(long_tool) if e["kind"] == "tool")
    assert len(tool_ev["detail"]) <= 141 and tool_ev["detail"].endswith("…")

    # seq numbers are dense and 1-based
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_drift_guard_non_sgr_ansi_handled_identically() -> None:
    """Both consumers must classify a ``\\x1b[2K``-prefixed glyph line.

    This is the exact divergence the review flagged: the old runner regex
    tolerated only SGR (``m``) ANSI and would MISS such a line, while the
    dashboard stripped all CSI. With the shared ``ANSI_RE`` both now see it.
    """
    line = "\x1b[2K\u2699 memory_search_nodes {\"query\":\"q\"}\n"
    assert extract_tool_names(line) == {"memory_search_nodes"}
    evs = parse_events(line)
    assert len(evs) == 1 and evs[0]["kind"] == "tool"
    assert evs[0]["detail"].startswith("memory_search_nodes")


def test_empty_and_unmapped() -> None:
    assert extract_tool_names("") == set()
    assert parse_events("") == []
    assert parse_events("plain narrative with no glyph\nanother line\n") == []


def test_ansi_re_is_broad_csi() -> None:
    # SGR, erase-line, and cursor-move CSI all stripped (not just SGR ``m``).
    assert ANSI_RE.sub("", "\x1b[0m\x1b[2K\x1b[1Afoo") == "foo"
