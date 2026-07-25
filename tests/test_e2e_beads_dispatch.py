"""E2E integration test of the BeadsLoop dispatch path.

Runs the REAL BeadsLoop poll thread against a REAL beads project built in a
tmp dir (real `br`/`bvr`/`git worktree`), with only the dispatch *target*
stubbed (via a test-only PROMPT_SCRIPT that runs real `br close <id>`) and the
external GitHub ops (`push_branch`/`create_pr`) mocked.

This is the fast (<30s), agent-drivable way to validate that the beads dispatch
system still works end-to-end after large refactors. Run with:
    uv run pytest tests/test_e2e_beads_dispatch.py -q
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings
from webhook_receiver.event_store import EventStore

REPO = Path(__file__).resolve().parent.parent
STUB = REPO / "tests" / "fixtures" / "stub-agent.ps1"


# ── environment helpers ────────────────────────────────────────────────────


def _run(
    args: list[str], *, cwd: str | Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=check)


def _br_json(project: Path, *args: str) -> object:
    """Run a `br` command with --json and return the parsed value.

    Handles both shapes br emits: a top-level object (`br create`) or a
    top-level array (`br ready`, `br show`).
    """
    r = _run(["br", *args, "--json"], cwd=project)
    if r.returncode != 0:
        raise RuntimeError(f"br {args} failed (exit {r.returncode}): {r.stderr}")
    return json.loads(r.stdout)


def _create_task(project: Path, title: str, priority: int) -> str:
    data = _br_json(project, "create", title, "-p", str(priority), "-d", "e2e task")
    if isinstance(data, dict) and "id" in data:
        return data["id"]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]["id"]
    raise RuntimeError(f"unexpected br create output: {data!r}")


def _make_project(tmp_base: Path, slug: str) -> tuple[Path, dict[str, str]]:
    """Build a real throwaway beads project under tmp_base/<slug>/.

    Returns (project_root, id_map). The project is a git repo with one commit
    (so `git worktree add` works), an initialized beads DB, and three seeded
    tasks with one dependency (T3 blocked by T1) to exercise selection ordering.
    """
    project = tmp_base / slug
    project.mkdir(parents=True, exist_ok=True)

    _run(["git", "init", "-q", "--initial-branch=main"], cwd=project)
    (project / "README.md").write_text("e2e\n")
    # The application plan MUST be committed (not just present on disk) so per-bead
    # worktrees — checked out from the default branch — inherit it. This is the
    # contract BeadsLoop._poll_and_process_project now enforces via _plan_tracked().
    plan_dir = project / "plan_docs"
    plan_dir.mkdir()
    (plan_dir / "application_plan.md").write_text("# e2e plan\n")
    _run(["git", "add", "."], cwd=project)
    _run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.email=e2e@test.local",
            "-c",
            "user.name=e2e",
            "commit",
            "-qm",
            "init",
        ],
        cwd=project,
    )

    _run(["br", "init"], cwd=project)
    db = project / ".beads" / "beads.db"
    if not db.exists():
        pytest.fail(f"br init did not create beads.db at {db}")

    t1 = _create_task(project, "Task A (first, unblocked)", 1)
    t2 = _create_task(project, "Task B (unblocked)", 2)
    t3 = _create_task(project, "Task C (blocked by A)", 2)
    # br dep add <BLOCKED> <BLOCKING>  ->  T3 is blocked by T1
    _run(["br", "dep", "add", t3, t1], cwd=project)

    return project, {"t1": t1, "t2": t2, "t3": t3}


def _settings(tmp_base: Path, project_root: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8080,
        github_webhook_secret="test-secret",
        opencode_server_url="http://localhost:4099",
        prompt_script=STUB,
        workspace=str(project_root),
        model="stub-model",
        agent="orchestrator",
        max_payload_chars=120000,
        max_body_bytes=25 * 1024 * 1024,
        log_level="warning",
        enable_simulator=False,
        beads_enabled=True,
        beads_poll_interval=1,
        beads_max_retries=3,
        beads_workspace_root=str(tmp_base),
    )


def _log_versions() -> None:
    for name in ("br", "bvr"):
        path = shutil.which(name)
        if not path:
            print(f"  {name}: not on PATH")
            continue
        try:
            r = _run([name, "--version"], cwd=".", check=False)
            print(f"  {name}: {r.stdout.strip() or r.stderr.strip()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: version check failed: {exc}")


def _has_event(store: EventStore, event_type: str, bead_id: str | None = None) -> bool:
    for e in store.recent(500):
        if e["type"] != event_type:
            continue
        if bead_id is None:
            return True
        if e.get("data", {}).get("bead_id") == bead_id:
            return True
    return False


def _event_index(store: EventStore, event_type: str, bead_id: str) -> int | None:
    """Return the 1-based sequence id of the first matching event, or None."""
    for e in store.recent(500):
        if e["type"] == event_type and e.get("data", {}).get("bead_id") == bead_id:
            return e["id"]
    return None


def _status_of(project: Path, bead_id: str) -> str:
    data = _br_json(project, "show", bead_id)
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, dict):
        issue = data.get("issue", data)
        return str(issue.get("status", "unknown")).lower()
    return "unknown"


def _diagnostics(
    store: EventStore,
    loop: BeadsLoop,
    project: Path,
    t1: str,
    *,
    logs: str = "",
    agent_io: dict[str, dict[str, str]] | None = None,
) -> str:
    lines = ["", "BeadsLoop e2e watchdog timeout — diagnostics:"]
    lines.append(f"  halted_beads={sorted(loop.halted_beads)}")
    lines.append(f"  retry_state={loop.retry_state}")

    try:
        ready = _run(["br", "ready", "--json"], cwd=project, check=False)
        lines.append(f"  br ready: {ready.stdout.strip()[:600]}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  br ready error: {exc}")

    t1_show = ""
    try:
        show = _run(["br", "show", t1, "--json"], cwd=project, check=False)
        t1_show = show.stdout.strip()
        lines.append(f"  br show {t1}: {t1_show[:600]}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  br show error: {exc}")

    t1_closed_in_br = '"status": "closed"' in t1_show or '"status":"closed"' in t1_show
    if t1_closed_in_br and not _has_event(store, "bead_closed", t1):
        lines.append(
            "  HINT: t1 is 'closed' in br but no bead_closed event was emitted. "
            "This strongly suggests `_check_bead_status` (beads_loop.py) misreads "
            "the real `br show --json` output shape (array vs dict)."
        )

    if agent_io:
        lines.append("  agent IO:")
        for bid, io in agent_io.items():
            lines.append(f"    [{bid}] stdout: {io.get('stdout', '')[:400]!r}")
            lines.append(f"    [{bid}] stderr: {io.get('stderr', '')[:400]!r}")

    if logs:
        lines.append("  captured logs (tail 30):")
        for ln in logs.splitlines()[-30:]:
            lines.append(f"    {ln}")

    evs = store.recent(500)
    lines.append(f"  events ({len(evs)}):")
    for e in evs[-40:]:
        lines.append(f"    [{e['id']}] {e['type']} {e.get('data', {})}")
    return "\n".join(lines)


def _read_agent_io(ids: dict[str, str]) -> dict[str, dict[str, str]]:
    """Read the loop's per-bead agent stdout/stderr temp files (best-effort).

    The loop writes /tmp/orchestrator-webhook/bead-<id>-<rand>.{stdout,stderr}
    (stems derived from its prompt-file mkstemp). Must be called BEFORE
    `_cleanup_loop_tempfiles` deletes them. Returns {bead_id: {stdout, stderr}}.
    """
    log_dir = Path("/tmp") / "orchestrator-webhook"
    io: dict[str, dict[str, str]] = {}
    if not log_dir.is_dir():
        return io
    for bid in ids.values():
        entry = {"stdout": "", "stderr": ""}
        for kind in ("stdout", "stderr"):
            matches = sorted(log_dir.glob(f"bead-{bid}-*.{kind}"))
            if matches:
                try:
                    entry[kind] = matches[-1].read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
        io[bid] = entry
    return io


def _cleanup_loop_tempfiles(ids: dict[str, str]) -> None:
    """Remove prompt/log temp files the loop writes under /tmp/orchestrator-webhook."""
    log_dir = Path("/tmp") / "orchestrator-webhook"
    if not log_dir.is_dir():
        return
    for bid in ids.values():
        for p in log_dir.glob(f"bead-{bid}-*"):
            try:
                p.unlink()
            except OSError:
                pass


# ── the test ───────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    shutil.which("pwsh") is None, reason="pwsh required for real _spawn_agent command"
)
@pytest.mark.skipif(shutil.which("br") is None, reason="br required for real beads")
def test_beads_loop_dispatches_and_closes_first_bead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    print("\n  tool versions:")
    _log_versions()

    tmp_base = tmp_path / "wsroot"
    tmp_base.mkdir()
    project, ids = _make_project(tmp_base, "e2eproj")
    t1 = ids["t1"]
    t1_safe = t1.replace("/", "-")

    settings = _settings(tmp_base, project)
    store = EventStore()
    loop = BeadsLoop(settings, event_store=store)

    # Capture loop log lines (incl. from the loop thread) for the diagnostics
    # dump on failure. DEBUG grabs selection/parse detail too.
    caplog.set_level(logging.DEBUG, logger="webhook_receiver.beads_loop")

    push_calls: list[tuple] = []
    pr_calls: list[tuple] = []
    # Patch at the LOOKUP site (the beads_loop module namespace), since the loop
    # imports push_branch/create_pr by name from webhook_receiver.workspace.
    monkeypatch.setattr(
        "webhook_receiver.beads_loop.push_branch",
        lambda *a, **k: push_calls.append(a),
    )
    monkeypatch.setattr(
        "webhook_receiver.beads_loop.create_pr",
        lambda *a, **k: pr_calls.append(a),
    )

    thread = threading.Thread(target=loop.run, daemon=True, name="e2e-beads-loop")
    agent_io: dict[str, dict[str, str]] = {}
    thread.start()
    try:
        timeout = float(os.environ.get("E2E_WATCHDOG_TIMEOUT", "60"))
        deadline = time.time() + timeout

        closed = False
        while time.time() < deadline:
            if _has_event(store, "bead_closed", t1):
                closed = True
                break
            time.sleep(0.2)

        # Light DAG-advance check: after t1 closes, the loop must select a
        # DIFFERENT ready bead on a subsequent poll. The stub auto-closes, so
        # this should happen within a couple poll intervals.
        advanced = False
        t1_close_seq = _event_index(store, "bead_closed", t1)
        grace = time.time() + 8
        while time.time() < grace:
            for e in store.recent(500):
                if e["type"] != "bead_picked_up":
                    continue
                if t1_close_seq is not None and e["id"] <= t1_close_seq:
                    continue
                if e.get("data", {}).get("bead_id") != t1:
                    advanced = True
                    break
            if advanced:
                break
            time.sleep(0.2)
    finally:
        loop.stop()
        thread.join(timeout=10)
        # Capture agent stdout/stderr BEFORE cleanup so diagnostics can show them.
        agent_io = _read_agent_io(ids)
        _cleanup_loop_tempfiles(ids)

    _diag_kwargs = dict(logs=caplog.text, agent_io=agent_io)

    if not closed:
        pytest.fail(_diagnostics(store, loop, project, t1, **_diag_kwargs))

    # AC #5: first bead completed via the real dispatch path.
    assert _status_of(project, t1) == "closed", "T1 should be closed in the beads DB"
    assert _has_event(store, "bead_picked_up", t1), "missing bead_picked_up for T1"
    assert _has_event(store, "agent_spawned", t1), "missing agent_spawned for T1"
    assert _has_event(store, "agent_completed", t1), "missing agent_completed for T1"
    assert _has_event(store, "bead_closed", t1), "missing bead_closed for T1"

    # push/PR invoked exactly once FOR T1 (mocked, no GitHub). The stub
    # auto-closes every bead it's handed, so the loop may drain additional beads
    # before we stop it; count only T1's calls (bead_id is the 2nd positional).
    t1_pushes = [c for c in push_calls if len(c) >= 2 and c[1] == t1]
    t1_prs = [c for c in pr_calls if len(c) >= 2 and c[1] == t1]
    assert len(t1_pushes) == 1, (
        f"expected 1 push_branch call for T1, got {len(t1_pushes)}: {push_calls}"
    )
    assert len(t1_prs) == 1, f"expected 1 create_pr call for T1, got {len(t1_prs)}: {pr_calls}"

    # Worktree was created then removed.
    wt = project / ".worktrees" / t1_safe
    assert not wt.exists(), f"worktree for T1 should be removed: {wt}"

    # AC #6: the loop advanced past T1 to a different ready bead.
    assert advanced, (
        "loop did not select a different bead after T1 closed (no later "
        "bead_picked_up event)" + _diagnostics(store, loop, project, t1, **_diag_kwargs)
    )

    # T1 must not be halted.
    assert not any(k.endswith(f":{t1}") for k in loop.halted_beads), (
        "T1 was halted: " + _diagnostics(store, loop, project, t1, **_diag_kwargs)
    )


# Stub prompt marker (tests/fixtures/stub-agent.ps1 extracts the bead id from
# the line "You have been assigned Bead <id>:"). If _build_bead_prompt's prose
# changes, this contract fails fast with a clear cause instead of a confusing
# e2e timeout where every bead "fails".
_STUB_MARKER_RE = re.compile(r"assigned Bead\s+([A-Za-z0-9._-]+):")


def test_bead_prompt_marker_matches_stub_regex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_bead_prompt must emit a marker the stub-agent.ps1 regex parses."""
    settings = _settings(tmp_path, tmp_path)
    loop = BeadsLoop(settings, event_store=EventStore())
    # progress_snapshot / context-file helpers may shell out to br; neutralize.
    monkeypatch.setattr(
        "webhook_receiver.beads_loop.subprocess.run",
        lambda *a, **k: MagicMock(stdout="[]", stderr="", returncode=0),
    )

    bead = {"id": "contract-abc123", "title": "Contract", "description": "x"}
    prompt = loop._build_bead_prompt(bead, 0, str(tmp_path), str(tmp_path))

    m = _STUB_MARKER_RE.search(prompt)
    assert m is not None, (
        "_build_bead_prompt no longer emits 'assigned Bead <id>:' — update the "
        "regex in tests/fixtures/stub-agent.ps1 or restore this marker."
    )
    assert m.group(1) == "contract-abc123", (
        f"stub marker regex captured {m.group(1)!r}, expected the bead id"
    )
