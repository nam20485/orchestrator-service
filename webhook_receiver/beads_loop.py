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

from webhook_receiver.config import Settings
from webhook_receiver.runner import _prompt_script_invocation, _stream_to_logger_and_file
from webhook_receiver.workspace import (
    cleanup_workspace,
    create_pr,
    create_workspace,
    push_branch,
)

logger = logging.getLogger(__name__)


class BeadsLoop:
    """Background thread that drains the Beads DAG via ``bvr --robot-next``.

    Uses bvr's graph-aware triage (PageRank, betweenness, blocker ratio) to
    select the highest-impact unblocked task, spawns an isolated agent for it,
    verifies closure via ``br show``, and handles retries with error context.
    Falls back to ``br ready --json`` + priority sort if bvr is unavailable.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._running = False
        self._active_beads: set[str] = set()
        self._lock = threading.Lock()
        self._retry_state: dict[str, dict[str, object]] = {}
        self._logged_init_warning = False

    def run(self) -> None:  # pragma: no cover (infinite loop — integration only)
        """Main loop — blocks until :meth:`stop` is called."""
        self._running = True
        logger.info(
            "BeadsLoop started (poll_interval=%ds workspace=%s target_repo=%s)",
            self._settings.beads_poll_interval,
            self._settings.beads_workspace_root,
            self._settings.beads_target_repo or "(none)",
        )
        while self._running:
            try:
                self._poll_and_process()
            except Exception:
                logger.exception("Error in BeadsLoop iteration")
            time.sleep(self._settings.beads_poll_interval)

    def stop(self) -> None:
        self._running = False

    def _poll_and_process(self) -> None:
        bead = self._get_next_bead()

        if bead is None:
            self._log_overview_if_idle()
            return

        bead_id = bead.get("id", "")
        if not bead_id:
            return

        with self._lock:
            if bead_id in self._active_beads:
                return
            self._active_beads.add(bead_id)

        try:
            self._process_bead(bead)
        finally:
            with self._lock:
                self._active_beads.discard(bead_id)

    # ── bead selection: bvr graph-aware first, br priority fallback ────────

    def _get_next_bead(self) -> dict | None:
        """Select the next bead to process.

        Tries ``bvr --robot-next`` (graph-aware) first.
        Falls back to ``br ready --json`` + priority sort if bvr fails.
        """
        bead = self._get_next_bead_bvr()
        if bead is not None:
            return bead

        ready = self._get_ready_beads()
        return self._select_next_bead(ready)

    def _get_next_bead_bvr(self) -> dict | None:
        """Query ``bvr --robot-next`` for the single highest-impact task."""
        try:
            result = self._run_beads_cmd(
                ["bvr", "--robot-next", "--format", "json"]
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

    def _log_overview_if_idle(self) -> None:
        """Log a compact project snapshot via ``bvr --robot-overview`` when idle."""
        try:
            result = self._run_beads_cmd(
                ["bvr", "--robot-overview", "--format", "json"]
            )
            stdout = result.stdout.strip()
            if stdout:
                logger.info("BeadsLoop idle — bvr overview: %s", stdout[:500])
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
            logger.debug("No beads ready (bvr overview unavailable)")

    def _get_ready_beads(self) -> list[dict]:
        """Query ``br ready --json`` for all open, unblocked tasks."""
        try:
            result = self._run_beads_cmd(["br", "ready", "--json"])
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

    def _process_bead(self, bead: dict) -> None:
        bead_id = bead.get("id", "")
        title = bead.get("title", bead_id)
        ws_root = self._settings.beads_workspace_root
        target_repo = self._settings.beads_target_repo

        if bead_id not in self._retry_state:
            self._retry_state[bead_id] = {"count": 0, "logs": ""}

        retries = self._retry_state[bead_id]["count"]
        if retries >= self._settings.beads_max_retries:
            logger.error(
                "Bead %s exceeded max retries (%d). Halting for human intervention.",
                bead_id,
                retries,
            )
            return

        ws_path = ws_root
        if target_repo:
            try:
                ws_path = create_workspace(ws_root, bead_id, target_repo)
            except Exception:
                logger.exception("Failed to create workspace for bead %s", bead_id)
                self._retry_state[bead_id]["count"] += 1
                return

        try:
            success, logs = self._spawn_agent(
                bead, ws_path, retries, self._retry_state[bead_id]["logs"]
            )

            if success:
                logger.info("Successfully completed bead %s", bead_id)
                if target_repo and ws_path != ws_root:
                    try:
                        push_branch(ws_path, bead_id)
                        create_pr(ws_path, bead_id, title)
                    except Exception:
                        logger.exception(
                            "Failed to push/create PR for bead %s", bead_id
                        )
                self._retry_state.pop(bead_id, None)
            else:
                logger.error(
                    "Agent failed to complete bead %s (attempt %d)",
                    bead_id,
                    retries + 1,
                )
                self._retry_state[bead_id]["count"] += 1
                self._retry_state[bead_id]["logs"] = (logs or "")[-3000:]
        finally:
            if target_repo and ws_path != ws_root:
                cleanup_workspace(ws_root, bead_id)

    def _build_bead_prompt(
        self, bead: dict, retry_count: int, previous_logs: str = ""
    ) -> str:
        """Build the agent prompt for a bead task (fully unit-testable)."""
        bead_id = bead.get("id", "")
        title = bead.get("title", bead_id)
        description = bead.get("description", "")

        prompt = (
            f"You have been assigned Bead {bead_id}: {title}.\n\n"
            f"Context & Requirements:\n{description}\n"
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
        previous_logs: str = "",
    ) -> tuple[bool, str]:
        bead_id = bead.get("id", "")

        prompt = self._build_bead_prompt(bead, retry_count, previous_logs)

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
        beads_db = os.path.join(
            self._settings.beads_workspace_root, ".beads", "beads.db"
        )
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

            status = self._check_bead_status(bead_id)
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

    def _check_bead_status(self, bead_id: str) -> str:
        """Query ``br show <id> --json`` and return the bead status."""
        try:
            result = self._run_beads_cmd(["br", "show", bead_id, "--json"])
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

    def _run_beads_cmd(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a ``br``/``bvr`` command with RUST_LOG=error for clean output."""
        return subprocess.run(
            args,
            cwd=self._settings.beads_workspace_root,
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
