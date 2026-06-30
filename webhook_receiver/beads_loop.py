from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

from webhook_receiver import bead_context
from webhook_receiver.config import Settings
from webhook_receiver.event_store import EventStore
from webhook_receiver.runner import _prompt_script_invocation, _stream_to_logger_and_file
from webhook_receiver.workspace import (
    create_bead_worktree,
    create_pr,
    discover_projects,
    project_workspace_path,
    push_branch,
    remove_bead_worktree,
)

logger = logging.getLogger(__name__)


class BeadsLoop:
    """Background thread that drains the Beads DAG for every project.

    Scans the workspace base dir for project subdirs (those containing a
    ``.beads/`` directory), then for each project uses ``bvr --robot-next``
    (graph-aware triage) to select the highest-impact unblocked task, spawns an
    isolated agent in a per-bead git worktree, verifies closure via ``br show``,
    and handles retries with error context.  Falls back to ``br ready --json``
    + priority sort if bvr is unavailable.
    """

    def __init__(self, settings: Settings, event_store: EventStore | None = None) -> None:
        self._settings = settings
        self._event_store = event_store
        self._running = False
        self._active_beads: set[str] = set()
        self._lock = threading.Lock()
        self._retry_state: dict[str, dict[str, object]] = {}
        self._bead_start_times: dict[str, float] = {}
        self._halted_beads: set[str] = set()
        self._bead_projects: dict[str, str] = {}
        self._logged_init_warning = False

    # ── public read-only properties for dashboard ──────────────────────────

    @property
    def active_beads(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._active_beads)

    @property
    def retry_state(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {k: dict(v) for k, v in self._retry_state.items()}

    @property
    def bead_start_times(self) -> dict[str, float]:
        with self._lock:
            return dict(self._bead_start_times)

    @property
    def halted_beads(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._halted_beads)

    @property
    def bead_projects(self) -> dict[str, str]:
        """Map of active bead IDs to their project slug."""
        with self._lock:
            return dict(self._bead_projects)

    def _emit(self, event_type: str, **data: object) -> None:
        if self._event_store:
            self._event_store.emit(event_type, **data)

    def run(self) -> None:  # pragma: no cover (infinite loop — integration only)
        """Main loop — blocks until :meth:`stop` is called."""
        self._running = True
        logger.info(
            "BeadsLoop started (poll_interval=%ds base=%s)",
            self._settings.beads_poll_interval,
            self._settings.beads_workspace_root,
        )
        while self._running:
            try:
                self._scan_and_process()
            except Exception:
                logger.exception("Error in BeadsLoop iteration")
            time.sleep(self._settings.beads_poll_interval)

    def stop(self) -> None:
        self._running = False

    def _scan_and_process(self) -> None:
        """Discover projects and process one bead per project (serial per project)."""
        base = self._settings.beads_workspace_root
        projects = discover_projects(base)
        for slug in projects:
            project_root = project_workspace_path(base, slug)
            try:
                self._poll_and_process_project(slug, project_root)
            except Exception:
                logger.exception(
                    "Error processing project=%s (%s)", slug, project_root
                )

    def _poll_and_process_project(
        self, project_slug: str, project_root: str
    ) -> None:
        """Poll one project for the next bead and process it."""
        bead = self._get_next_bead(project_root)

        if bead is None:
            self._log_overview_if_idle(project_root)
            return

        bead_id = bead.get("id", "")
        if not bead_id:
            return

        key = self._key(project_slug, bead_id)

        with self._lock:
            if key in self._active_beads or key in self._halted_beads:
                return
            self._active_beads.add(key)
            self._bead_start_times[key] = time.time()
            self._bead_projects[key] = project_slug

        self._emit(
            "bead_picked_up",
            bead_id=bead_id,
            title=bead.get("title", bead_id),
            priority=bead.get("priority"),
            project=project_slug,
        )

        try:
            self._process_bead(bead, project_slug, project_root)
        finally:
            with self._lock:
                self._active_beads.discard(key)
                self._bead_start_times.pop(key, None)
                self._bead_projects.pop(key, None)

    # ── bead selection: bvr graph-aware first, br priority fallback ────────

    def _get_next_bead(self, project_root: str) -> dict | None:
        """Select the next bead to process for *project_root*.

        Tries ``bvr --robot-next`` (graph-aware) first.
        Falls back to ``br ready --json`` + priority sort if bvr fails.
        """
        bead = self._get_next_bead_bvr(project_root)
        if bead is not None:
            return bead

        ready = self._get_ready_beads(project_root)
        return self._select_next_bead(ready)

    def _get_next_bead_bvr(self, project_root: str) -> dict | None:
        """Query ``bvr --robot-next`` for the single highest-impact task."""
        try:
            result = self._run_beads_cmd(
                ["bvr", "--robot-next", "--format", "json"], project_root
            )
        except FileNotFoundError:
            logger.debug("bvr not found — falling back to br ready")
            return None
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if self._is_not_initialized(stderr):
                self._log_init_warning_once()
                return None
            logger.warning("bvr --robot-next failed: %s", stderr)
            return None

        stdout = result.stdout.strip()
        if not stdout:
            return None

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            logger.warning("bvr --robot-next returned invalid JSON")
            return None

        return _extract_bead(data)

    def _log_overview_if_idle(self, project_root: str) -> None:
        """Log a compact project snapshot via ``bvr --robot-overview`` when idle."""
        try:
            result = self._run_beads_cmd(
                ["bvr", "--robot-overview", "--format", "json"], project_root
            )
            stdout = result.stdout.strip()
            if stdout:
                logger.info(
                    "BeadsLoop idle — bvr overview (%s): %s",
                    os.path.basename(project_root),
                    stdout[:500],
                )
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
            logger.debug("No beads ready (bvr overview unavailable)")

    def _get_ready_beads(self, project_root: str) -> list[dict]:
        """Query ``br ready --json`` for all open, unblocked tasks."""
        try:
            result = self._run_beads_cmd(["br", "ready", "--json"], project_root)
        except FileNotFoundError:
            logger.warning("br not found — skipping BeadsLoop poll")
            return []
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            if self._is_not_initialized(stderr):
                self._log_init_warning_once()
                return []
            logger.error("br ready failed: %s", stderr)
            return []

        stdout = result.stdout.strip()
        if not stdout:
            return []

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            logger.error("br ready returned invalid JSON")
            return []

        if isinstance(data, dict):
            issues = data.get("issues", data.get("beads", []))
        elif isinstance(data, list):
            issues = data
        else:
            issues = []

        return [b for b in issues if isinstance(b, dict)]

    def _select_next_bead(self, beads: list[dict]) -> dict | None:
        if not beads:
            return None
        return min(beads, key=lambda b: b.get("priority", 999))

    # ── bead processing ────────────────────────────────────────────────────

    def _process_bead(
        self, bead: dict, project_slug: str, project_root: str
    ) -> None:
        bead_id = bead.get("id", "")
        title = bead.get("title", bead_id)
        key = self._key(project_slug, bead_id)

        if key not in self._retry_state:
            with self._lock:
                self._retry_state.setdefault(key, {"count": 0, "logs": ""})

        with self._lock:
            retries = self._retry_count(self._retry_state[key])
        if retries >= self._settings.beads_max_retries:
            logger.error(
                "Bead %s exceeded max retries (%d). Halting for human intervention.",
                bead_id,
                retries,
            )
            self._emit(
                "bead_halted",
                bead_id=bead_id,
                reason="max_retries_exceeded",
                retries=retries,
                project=project_slug,
            )
            with self._lock:
                self._halted_beads.add(key)
            return

        ws_path: str | None = None
        try:
            ws_path = create_bead_worktree(project_root, bead_id)
        except subprocess.CalledProcessError as exc:
            # Surface the captured git stderr so failures (e.g. "fatal:
            # detected dubious ownership", branch/ref errors) are actionable
            # instead of just an opaque exit code + traceback.
            logger.error(
                "Failed to create worktree for bead %s (git exit %d).\n"
                "command: %s\nstderr: %s",
                bead_id,
                exc.returncode,
                " ".join(exc.cmd or []),
                (exc.stderr or "").strip(),
            )
            with self._lock:
                self._retry_state[key]["count"] = (
                    self._retry_count(self._retry_state[key]) + 1
                )
            return
        except Exception:
            logger.exception("Failed to create worktree for bead %s", bead_id)
            with self._lock:
                self._retry_state[key]["count"] = (
                    self._retry_count(self._retry_state[key]) + 1
                )
            return

        try:
            with self._lock:
                prev_logs = str(self._retry_state[key].get("logs", ""))
            success, logs = self._spawn_agent(
                bead, ws_path, retries, project_root, prev_logs
            )

            if success:
                logger.info("Successfully completed bead %s", bead_id)
                self._emit("bead_closed", bead_id=bead_id, project=project_slug)
                try:
                    push_branch(ws_path, bead_id)
                    create_pr(ws_path, bead_id, title)
                except Exception:
                    logger.exception(
                        "Failed to push/create PR for bead %s", bead_id
                    )
                with self._lock:
                    self._retry_state.pop(key, None)
            else:
                logger.error(
                    "Agent failed to complete bead %s (attempt %d)",
                    bead_id,
                    retries + 1,
                )
                with self._lock:
                    self._retry_state[key]["count"] = (
                        self._retry_count(self._retry_state[key]) + 1
                    )
                    self._retry_state[key]["logs"] = (logs or "")[-3000:]
                self._emit(
                    "bead_failed",
                    bead_id=bead_id,
                    attempt=retries + 1,
                    max_retries=self._settings.beads_max_retries,
                    project=project_slug,
                )
        finally:
            remove_bead_worktree(project_root, bead_id)

    def _build_bead_prompt(
        self,
        bead: dict,
        retry_count: int,
        ws_path: str,
        project_root: str,
        previous_logs: str = "",
    ) -> str:
        """Build the agent prompt for a bead task (fully unit-testable).

        Writes durable context (project overview + tooling) into the workspace as
        ``BEADS_AGENT_GUIDE.md`` (and ``AGENTS.md`` when absent), then assembles a
        prompt that points the agent at those files and embeds the volatile progress
        snapshot. Context errors never block spawning — on failure the prompt falls
        back to the minimal task description.
        """
        bead_id = bead.get("id", "")
        title = bead.get("title", bead_id)
        description = bead.get("description", "")

        try:
            guide = bead_context.build_agent_guide(ws_path)
            bead_context.write_context_files(ws_path, guide)
        except Exception:  # noqa: BLE001 — context files are best-effort
            logger.debug(
                "Failed to write bead context files for %s", bead_id, exc_info=True
            )

        try:
            snapshot = bead_context.progress_snapshot(
                ws_path, bead_id, lambda args: self._run_beads_cmd(args, project_root)
            )
        except Exception:  # noqa: BLE001 — snapshot is best-effort
            snapshot = "(Progress snapshot unavailable.)"

        prompt = (
            f"You have been assigned Bead {bead_id}: {title}.\n\n"
            f"## Project & Tooling Context\n"
            f"This workspace contains `BEADS_AGENT_GUIDE.md` and "
            f"`plan_docs/application_plan.md` — READ THEM FIRST for the full "
            f"application overview, available commands (`br`/`bvr`), and the "
            f"completion workflow.\n\n"
            f"## Progress\n{snapshot}\n\n"
            f"## Context & Requirements\n{description}\n"
        )
        if previous_logs:
            prompt += (
                f"\n\nWARNING: Your previous attempt failed. Review logs:\n"
                f"{previous_logs}\n\n"
                f"Fix the code, ensure tests pass, and run `br close {bead_id}`."
            )
        else:
            prompt += (
                f"\n\nWhen completed and ALL tests pass, you MUST run: "
                f"`br close {bead_id}`."
            )
        return prompt

    def _spawn_agent(  # pragma: no cover (subprocess integration — tested via mocks)
        self,
        bead: dict,
        ws_path: str,
        retry_count: int,
        project_root: str,
        previous_logs: str = "",
    ) -> tuple[bool, str]:
        bead_id = bead.get("id", "")

        prompt = self._build_bead_prompt(
            bead, retry_count, ws_path, project_root, previous_logs
        )

        logger.info(
            "Injecting prompt for bead %s into service (attempt %d, workspace=%s)",
            bead_id,
            retry_count + 1,
            ws_path,
        )

        log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
        log_dir.mkdir(parents=True, exist_ok=True)

        fd, prompt_name = tempfile.mkstemp(
            prefix=f"bead-{bead_id}-", suffix=".md", dir=log_dir
        )
        prompt_path = Path(prompt_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(prompt)

        modified = replace(self._settings, workspace=ws_path)
        cmd = _prompt_script_invocation(modified, prompt_path)

        env = os.environ.copy()
        beads_db = os.path.join(project_root, ".beads", "beads.db")
        env["BD_DB"] = beads_db

        stdout_path = log_dir / f"{prompt_path.stem}.stdout"
        stderr_path = log_dir / f"{prompt_path.stem}.stderr"
        stdout_file = open(stdout_path, "w", encoding="utf-8")
        stderr_file = open(stderr_path, "w", encoding="utf-8")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True,
                env=env,
            )

            self._emit(
                "agent_spawned",
                bead_id=bead_id,
                pid=proc.pid,
                attempt=retry_count + 1,
                workspace=ws_path,
            )

            _agent_start = time.time()

            t1 = threading.Thread(
                target=_stream_to_logger_and_file,
                args=(proc.stdout, stdout_file, f"bead-{bead_id}"),
                daemon=True,
            )
            t2 = threading.Thread(
                target=_stream_to_logger_and_file,
                args=(proc.stderr, stderr_file, f"bead-{bead_id}-err"),
                daemon=True,
            )
            t1.start()
            t2.start()

            proc.wait()
            t1.join()
            t2.join()

            _duration = time.time() - _agent_start
            status = self._check_bead_status(bead_id, project_root)
            self._emit(
                "agent_completed",
                bead_id=bead_id,
                exit_code=proc.returncode,
                duration_s=round(_duration, 2),
                status=status,
            )
            if status != "closed":
                logger.warning(
                    "Bead %s is still %s after agent exit.", bead_id, status
                )
                err_logs = stderr_path.read_text(encoding="utf-8", errors="replace")
                return False, err_logs

            return True, ""

        except Exception as exc:
            logger.error("Error executing prompt for bead %s: %s", bead_id, exc)
            return False, str(exc)

    def _check_bead_status(self, bead_id: str, project_root: str) -> str:
        """Query ``br show <id> --json`` and return the bead status."""
        try:
            result = self._run_beads_cmd(
                ["br", "show", bead_id, "--json"], project_root
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return "unknown"

        stdout = result.stdout.strip()
        if not stdout:
            return "unknown"

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return "unknown"

        if isinstance(data, dict):
            issue = data.get("issue", data)
            return str(issue.get("status", "unknown")).lower()

        return "unknown"

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _key(project_slug: str, bead_id: str) -> str:
        """Composite key for project-scoped loop state."""
        return f"{project_slug}:{bead_id}"

    @staticmethod
    def _retry_count(state: dict[str, object]) -> int:
        """Extract the integer retry count from a retry-state dict."""
        raw = state.get("count", 0)
        return int(raw) if isinstance(raw, (int, float)) else 0

    _NOT_INITIALIZED_SIGNATURES = (
        "NOT_INITIALIZED",
        "no workspace config or single-repo beads data could be resolved",
    )

    def _is_not_initialized(self, stderr: str) -> bool:
        """Return True if the stderr indicates beads is not initialized."""
        return any(sig in stderr for sig in self._NOT_INITIALIZED_SIGNATURES)

    def _log_init_warning_once(self) -> None:
        """Log a one-time INFO that beads is not initialized (normal state)."""
        if not self._logged_init_warning:
            logger.info(
                "Beads not initialized at %s — waiting for /plan-to-beads",
                self._settings.beads_workspace_root,
            )
            self._logged_init_warning = True

    def _run_beads_cmd(
        self, args: list[str], cwd: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a ``br``/``bvr`` command with RUST_LOG=error for clean output."""
        return subprocess.run(
            args,
            cwd=cwd or self._settings.beads_workspace_root,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "RUST_LOG": "error"},
        )


def _extract_bead(data: object) -> dict | None:
    """Extract a bead dict from various bvr JSON output shapes."""
    if not isinstance(data, dict):
        return None

    for key in ("bead", "recommendation", "issue", "next"):
        nested = data.get(key)
        if isinstance(nested, dict) and "id" in nested:
            return nested

    if "id" in data:
        return data

    issues = data.get("issues")
    if isinstance(issues, list) and issues and isinstance(issues[0], dict):
        return issues[0]

    return None
