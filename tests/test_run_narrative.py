from __future__ import annotations

from webhook_receiver.run_narrative import parse_narrative

# A synthetic stderr sample that mirrors the real opencode client format.
# Includes boot noise (to be dropped), model marker, delegations, tool calls,
# reads, errors, watchdog, and exit.
_SAMPLE_STDERR = (
    "INFO  2026-07-22T19:07:02 service=default args=[...]\n"            # noise
    "sqlite-migration:done\n"                                            # noise
    "\x1b[0m> orchestrator \xc2\xb7 glm-5\x1b[0m\n"                      # model
    "\x1b[0m\u2699 \x1b[0mbash {\"command\":\"git status\"}\n"
    "\x1b[0m\u2192 \x1b[0mRead /workspace/repo/AGENTS.md\n"
    "\x1b[0m\u2192 \x1b[0mRead /workspace/repo/README.md\n"
    "\x1b[0m\u2192 \x1b[0mRead /workspace/repo/pyproject.toml\n"
    "\x1b[0m\u2022 \x1b[0mRun tests\x1b[90m Execute Agent\x1b[0m\n"
    "\x1b[0m\u2713 \x1b[0mRun tests\x1b[90m Execute Agent\x1b[0m\n"
    "\x1b[0m\u2717 \x1b[0mInvalid Tool unavailable 'foo'\n"
    "[watchdog] client output idle 30s\n"
    "\x1b[0mprocess exited with code 0\n"
)

_MANIFEST_COMPLETED = {
    "stem": "prompt-test__issue-1__adhoc__20260722T190702Z",
    "started_at": "2026-07-22T19:07:02Z",
    "ended_at": "2026-07-22T19:09:36Z",
    "exit_code": 0,
    "classification": "completed",
    "tools": ["bash", "read"],
}

_MANIFEST_FAILED = {
    "stem": "prompt-test",
    "started_at": "2026-07-22T19:07:02Z",
    "ended_at": "2026-07-22T19:08:00Z",
    "exit_code": 1,
    "classification": "failed",
}

_MANIFEST_RUNNING = {
    "stem": "prompt-test",
    "started_at": "2026-07-22T19:07:02Z",
}

_MANIFEST_TIMEOUT = {
    "stem": "prompt-test",
    "started_at": "2026-07-22T19:07:02Z",
    "ended_at": "2026-07-22T19:22:02Z",
    "exit_code": -9,
    "classification": "idle_timeout",
    "kill_reason": "idle_timeout",
}

_MANIFEST_ZERO_WORK = {
    "stem": "prompt-test",
    "started_at": "2026-07-22T19:07:02Z",
    "ended_at": "2026-07-22T19:08:00Z",
    "exit_code": 0,
    "classification": "zero_work",
    "tools": ["read", "grep"],
}


# ── structure ──────────────────────────────────────────────────────────────


def test_narrative_has_expected_top_level_keys() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    assert "summary" in result
    assert "timeline" in result
    assert "stats" in result


def test_summary_completed_status() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    s = result["summary"]
    assert s["status"] == "completed"
    assert s["exit_code"] == 0
    assert s["duration_s"] is not None
    assert s["duration_s"] > 0


def test_summary_failed_status() -> None:
    result = parse_narrative("", _MANIFEST_FAILED)
    assert result["summary"]["status"] == "failed"
    assert "Failed" in result["summary"]["exit_message"]


def test_summary_running_status() -> None:
    result = parse_narrative("", _MANIFEST_RUNNING)
    assert result["summary"]["status"] == "running"
    assert "in progress" in result["summary"]["exit_message"].lower()


def test_summary_timeout_status() -> None:
    result = parse_narrative("", _MANIFEST_TIMEOUT)
    assert result["summary"]["status"] == "timeout"
    assert "Watchdog" in result["summary"]["exit_message"]


def test_summary_zero_work_status() -> None:
    result = parse_narrative("", _MANIFEST_ZERO_WORK)
    assert result["summary"]["status"] == "zero_work"
    assert "no execution tools" in result["summary"]["exit_message"].lower()


# ── timeline ───────────────────────────────────────────────────────────────


def test_timeline_groups_consecutive_reads() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    tl = result["timeline"]
    # Find the grouped read entry
    read_entries = [e for e in tl if e["kind"] == "read"]
    assert len(read_entries) == 1
    assert "3 files" in read_entries[0]["summary"]
    assert "items" in read_entries[0]
    assert len(read_entries[0]["items"]) == 3


def test_timeline_preserves_delegations() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    tl = result["timeline"]
    delegations = [e for e in tl if e["kind"] == "delegation"]
    assert len(delegations) == 1
    assert delegations[0]["agent"] == "Execute"


def test_timeline_includes_errors_and_watchdog() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    tl = result["timeline"]
    kinds = {e["kind"] for e in tl}
    assert "error" in kinds
    assert "watchdog" in kinds


# ── stats ──────────────────────────────────────────────────────────────────


def test_stats_counts_match_events() -> None:
    result = parse_narrative(_SAMPLE_STDERR, _MANIFEST_COMPLETED)
    stats = result["stats"]
    assert stats["files_read"] == 3
    assert stats["delegations"] == 1
    assert stats["errors"] == 1
    assert stats["tool_calls"] == 1  # bash
    assert stats["watchdog"] == 1
    assert stats["total_events"] > 0


# ── edge cases ─────────────────────────────────────────────────────────────


def test_empty_stderr() -> None:
    result = parse_narrative("", _MANIFEST_COMPLETED)
    assert result["timeline"] == []
    assert result["stats"]["total_events"] == 0


def test_no_manifest_fields() -> None:
    result = parse_narrative("", {})
    assert result["summary"]["status"] == "running"
    assert result["summary"]["duration_s"] is None
