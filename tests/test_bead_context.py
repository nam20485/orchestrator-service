from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from webhook_receiver import bead_context


def _result(stdout: str = "", returncode: int = 0) -> MagicMock:
    res = MagicMock()
    res.stdout = stdout
    res.stderr = ""
    res.returncode = returncode
    return res


def _runner(stdout_by_substr: dict[str, str]) -> Any:
    """Return a fake run_beads that picks stdout by matching a substring in argv."""

    def _run(argv: list[str]) -> MagicMock:
        joined = " ".join(argv)
        for substr, out in stdout_by_substr.items():
            if substr in joined:
                return _result(out)
        return _result("")

    return _run


# ── read_project_overview ──────────────────────────────────────────────────


def test_read_overview_missing_returns_fallback(tmp_path: Path) -> None:
    out = bead_context.read_project_overview(str(tmp_path))
    assert "No application_plan.md found" in out


def test_read_overview_reads_plan(tmp_path: Path) -> None:
    (tmp_path / "plan_docs").mkdir()
    (tmp_path / "plan_docs" / "application_plan.md").write_text(
        "# My App\n\nA great app.", encoding="utf-8"
    )
    out = bead_context.read_project_overview(str(tmp_path))
    assert "My App" in out
    assert "A great app." in out
    assert "truncated" not in out


def test_read_overview_truncates_long_plan(tmp_path: Path) -> None:
    (tmp_path / "plan_docs").mkdir()
    long_body = "x" * 5000
    (tmp_path / "plan_docs" / "application_plan.md").write_text(long_body)
    out = bead_context.read_project_overview(str(tmp_path), limit=100)
    assert "truncated" in out
    assert len(out) < len(long_body)


# ── progress_snapshot ──────────────────────────────────────────────────────


def test_snapshot_counts_open_and_closed() -> None:
    beads = {
        "issues": [
            {"id": "br-1", "status": "closed", "title": "A"},
            {"id": "br-2", "status": "closed", "title": "B"},
            {"id": "br-3", "status": "open", "title": "C"},
            {"id": "br-4", "status": "blocked", "title": "D"},
        ]
    }
    runner = _runner({"list": json.dumps(beads), "graph": ""})
    out = bead_context.progress_snapshot("/workspace", "br-3", runner)
    assert "2/4 beads closed" in out
    assert "1 open" in out
    # A "blocked" bead must NOT be counted as open.
    assert "4 open" not in out
    assert "other states" in out
    # No graph → no blockers line.
    assert "prerequisites" not in out


def test_snapshot_includes_blockers_with_status() -> None:
    beads = {
        "issues": [
            {"id": "br-a", "status": "closed", "title": "dep"},
            {"id": "br-b", "status": "closed", "title": "dep2"},
            {"id": "br-c", "status": "open", "title": "current"},
        ]
    }
    graph = {
        "components": [
            {
                "nodes": [{"id": "br-c"}],
                # [dependent, dependency] → br-c blocked by br-a and br-b
                "edges": [["br-c", "br-a"], ["br-c", "br-b"]],
            }
        ]
    }
    runner = _runner(
        {"list": json.dumps(beads), "graph": json.dumps(graph)}
    )
    out = bead_context.progress_snapshot("/workspace", "br-c", runner)
    assert "prerequisites" in out
    assert "br-a [closed]" in out
    assert "br-b [closed]" in out


def test_snapshot_runner_failure_degrades_gracefully() -> None:
    def _raise(_argv: list[str]) -> Any:
        raise subprocess.CalledProcessError(1, "br")

    out = bead_context.progress_snapshot("/workspace", "br-1", _raise)
    assert "0/0 beads closed" in out


def test_snapshot_file_not_found_degrades() -> None:
    def _raise(_argv: list[str]) -> Any:
        raise FileNotFoundError

    out = bead_context.progress_snapshot("/workspace", "br-1", _raise)
    assert "0/0 beads closed" in out


# ── build_agent_guide ──────────────────────────────────────────────────────


def test_guide_contains_overview_and_tooling(tmp_path: Path) -> None:
    (tmp_path / "plan_docs").mkdir()
    (tmp_path / "plan_docs" / "application_plan.md").write_text(
        "# Widget Service\n\nMicroservice for widgets.", encoding="utf-8"
    )
    guide = bead_context.build_agent_guide(str(tmp_path))
    assert "Widget Service" in guide
    assert "br close" in guide
    assert "Do NOT push" in guide


def test_guide_falls_back_when_no_plan(tmp_path: Path) -> None:
    guide = bead_context.build_agent_guide(str(tmp_path))
    assert "No application_plan.md found" in guide
    assert "br close" in guide


# ── write_context_files ────────────────────────────────────────────────────


def test_write_files_creates_guide_and_agents(tmp_path: Path) -> None:
    guide = "GUIDE BODY"
    actions = bead_context.write_context_files(str(tmp_path), guide)
    assert (tmp_path / "BEADS_AGENT_GUIDE.md").read_text() == "GUIDE BODY"
    # Bare workspace → AGENTS.md written for native opencode auto-load.
    assert (tmp_path / "AGENTS.md").read_text() == "GUIDE BODY"
    assert any(v == "written" for v in actions.values())


def test_write_files_does_not_clobber_existing_agents(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("REPO OWN INSTRUCTIONS", encoding="utf-8")
    actions = bead_context.write_context_files(str(tmp_path), "GUIDE BODY")
    # Repo AGENTS.md preserved.
    assert (tmp_path / "AGENTS.md").read_text() == "REPO OWN INSTRUCTIONS"
    # Guide still written.
    assert (tmp_path / "BEADS_AGENT_GUIDE.md").read_text() == "GUIDE BODY"
    assert any(
        "AGENTS.md" in path and action == "skipped"
        for path, action in actions.items()
    )
