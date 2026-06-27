"""Context injection for bead-execution agents.

Provides durable project/tooling context (written as workspace files) and a volatile
per-bead progress snapshot (returned as a string for the agent prompt). All ``br``
interaction goes through an injectable ``run_beads`` callable so the helpers stay
pure and unit-testable.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_OVERVIEW_LIMIT = 4000
_GUIDE_FILENAME = "BEADS_AGENT_GUIDE.md"
_AGENTS_FILENAME = "AGENTS.md"

# A beads runner takes a ``br``/``bvr`` argv list and returns a
# ``CompletedProcess`` (mirrors ``BeadsLoop._run_beads_cmd``).
BeadsRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


TOOLING_REFERENCE = """\
## Tooling & Commands

The `br` (beads) and `bvr` CLI tools are installed. Always run `br`/`bvr` from this
workspace directory so it resolves the local `.beads/` graph.

Essential commands:
- `br show <id>`          — full detail of a bead (status, description, deps).
- `br close <id>`         — mark your bead complete (run AFTER all tests pass).
- `br ready --json`       — list currently unblocked tasks.
- `br list --json`        — list every bead with its status.
- `bvr --robot-overview`  — compact project / DAG snapshot.

Completion workflow:
- Implement the task, then run the project's own test/validation suite (see AGENTS.md /
  README in this workspace; e.g. `./scripts/validate.ps1 -All` when present).
- Commit your work on the current task branch.
- Do NOT push the branch or open a pull request — the orchestrator performs `git push`
  and `gh pr create` automatically after you run `br close <id>`.

Filesystem hints:
- `/app`           — OpenCode install + config (opencode.json, AGENTS.md, .opencode/).
- your workspace   — the working directory for code; `.beads/` holds the DAG here.
"""


def _parse_beads(stdout: str) -> list[dict[str, Any]]:
    """Parse ``br``/``bvr`` JSON output into a list of bead dicts (defensive)."""
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("issues", data.get("beads", []))
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [b for b in items if isinstance(b, dict)]


def read_project_overview(ws_path: str, limit: int = _OVERVIEW_LIMIT) -> str:
    """Read the application plan overview from the workspace, truncated to *limit*.

    Returns a fallback note when the plan is missing, empty, or unreadable.
    """
    plan = Path(ws_path) / "plan_docs" / "application_plan.md"
    try:
        text = plan.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "(No application_plan.md found in this workspace.)"
    text = text.strip()
    if not text:
        return "(No application_plan.md found in this workspace.)"
    if len(text) > limit:
        return (
            text[:limit].rstrip()
            + "\n\n(truncated — see plan_docs/application_plan.md for the full plan.)"
        )
    return text


def _blockers_for(bead_id: str, run_beads: BeadsRunner) -> list[str]:
    """Return the direct blocking dependency IDs for *bead_id* via ``br graph``.

    Never raises: any error or missing graph yields an empty list.
    """
    try:
        graph_res = run_beads(["br", "graph", "--all", "--json"])
        graph_out = (getattr(graph_res, "stdout", "") or "").strip()
    except (FileNotFoundError, subprocess.CalledProcessError, AttributeError):
        return []
    if not graph_out:
        return []
    try:
        gdata = json.loads(graph_out)
    except json.JSONDecodeError:
        return []
    if not isinstance(gdata, dict):
        return []

    blockers: list[str] = []
    for comp in gdata.get("components", []) or []:
        if not isinstance(comp, dict):
            continue
        for edge in comp.get("edges", []) or []:
            # ``br`` emits edges as ``[dependent_id, dependency_id]``:
            # the dependent is blocked by the dependency.
            if isinstance(edge, (list, tuple)) and len(edge) == 2:
                dependent, dependency = edge[0], edge[1]
                if dependent == bead_id and dependency not in blockers:
                    blockers.append(dependency)
    return blockers


def progress_snapshot(ws_path: str, bead_id: str, run_beads: BeadsRunner) -> str:
    """Build a compact, bounded progress string for *bead_id*.

    Reports closed/open counts and this bead's direct blockers with their statuses.
    Never raises: any ``br`` failure degrades to a minimal string.
    """
    try:
        list_res = run_beads(["br", "list", "--json"])
        beads = _parse_beads((getattr(list_res, "stdout", "") or "").strip())
    except (FileNotFoundError, subprocess.CalledProcessError, AttributeError):
        beads = []

    total = len(beads)
    closed = sum(1 for b in beads if str(b.get("status", "")).lower() == "closed")
    open_ = sum(1 for b in beads if str(b.get("status", "")).lower() == "open")
    other = total - closed - open_

    lines = [f"Progress: {closed}/{total} beads closed, {open_} open."]
    if other:
        lines.append(
            f"({other} in other states: ready/blocked/halted/active.)"
        )

    status_by_id = {b.get("id"): str(b.get("status", "")).lower() for b in beads}
    blockers = _blockers_for(bead_id, run_beads)
    if blockers:
        lines.append("This bead's prerequisites (already complete; their output is available):")
        for bid in blockers:
            lines.append(f"  - {bid} [{status_by_id.get(bid, 'unknown')}]")
    return "\n".join(lines)


def build_agent_guide(ws_path: str) -> str:
    """Assemble the durable agent guide markdown (overview + tooling + workflow)."""
    overview = read_project_overview(ws_path)
    return (
        "# Bead Agent Guide\n\n"
        "This workspace is being built bead-by-bead (atomic tasks) via the beads DAG.\n"
        "Read this file for project context, available tooling, and the completion workflow.\n\n"
        "## Project Overview\n\n"
        f"{overview}\n\n"
        f"{TOOLING_REFERENCE}"
    )


def write_context_files(ws_path: str, guide: str) -> dict[str, str]:
    """Write ``BEADS_AGENT_GUIDE.md`` (always) and ``AGENTS.md`` (only if absent).

    Never clobbers an existing ``AGENTS.md`` so cloned repos keep their own
    repository instructions. Returns ``{path: "written"|"skipped"}``. Failures are
    logged and do not raise — context files are best-effort.
    """
    base = Path(ws_path)
    actions: dict[str, str] = {}
    try:
        base.mkdir(parents=True, exist_ok=True)

        guide_path = base / _GUIDE_FILENAME
        guide_path.write_text(guide, encoding="utf-8")
        actions[str(guide_path)] = "written"

        agents_path = base / _AGENTS_FILENAME
        if agents_path.exists():
            actions[str(agents_path)] = "skipped"
        else:
            agents_path.write_text(guide, encoding="utf-8")
            actions[str(agents_path)] = "written"
    except OSError:
        logger.warning("Could not write bead context files to %s", ws_path, exc_info=True)
    return actions
