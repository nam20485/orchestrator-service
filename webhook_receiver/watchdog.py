"""Activity-aware idle watchdog for dispatched opencode runs.

Replaces the previous single ``proc.wait(timeout=...)`` approach with
three-tier monitoring adapted from the battle-tested bash watchdog in
``intel-agency/workflow-orchestration-service`` (see
``docs/idle-timeout-implementation-report.md``).

The old system ran the opencode client and server in the same container and
monitored the server process's ``/proc/<pid>/io`` counters. The new system runs
them in separate Docker containers, so ``/proc`` is inaccessible. Instead the
watchdog monitors the **opencode CLI's stdout/stderr line freshness** — the
stream readers (:func:`webhook_receiver.runner._stream_to_logger_and_file`)
update a shared :class:`WatchdogState` on every line received.

Three independent kill conditions:

1. **Idle timeout** — no stdout/stderr output for ``IDLE_TIMEOUT_SECS``. This
   catches rate-limited API calls (the CLI blocks silently waiting for the
   server), stuck permission prompts, and genuine stalls.

2. **Consecutive errors** — ``MAX_CONSECUTIVE_ERRORS`` error lines without an
   intervening non-error line. Catches repeated tool failures, memory write
   errors, and (when the CLI forwards them) server-side API errors.

3. **Hard ceiling** — absolute maximum runtime regardless of activity. Safety
   net so a run that is technically "active" (producing output) but making no
   real progress cannot run forever.

Live heartbeat tracing (``[watchdog]`` lines in the container log) provides
post-mortem diagnosis without requiring debug mode.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Signal classification ─────────────────────────────────────────────────

# Error indicators that may appear in the opencode CLI's stderr/stdout.
# Server-side errors (``level=ERROR``) are included because some CLI
# configurations forward server logs to the client stream.
_ERROR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"level=ERROR", re.IGNORECASE),
    re.compile(r"AI_APICallError", re.IGNORECASE),
    re.compile(r"Usage limit reached", re.IGNORECASE),
    re.compile(r"Rate limit", re.IGNORECASE),
    re.compile(r"^\s*Error:", re.IGNORECASE),
    re.compile(r"✗"),  # opencode error glyph
    re.compile(r"\bfailed\b", re.IGNORECASE),
]


class SignalKind(Enum):
    """Classification of a single stdout/stderr line."""

    NORMAL = "normal"
    ERROR = "error"


def classify_line(line: str) -> SignalKind:
    """Return whether *line* is an error signal or normal output.

    Error lines increment the consecutive-error counter; normal lines reset it.
    """
    return (
        SignalKind.ERROR
        if any(p.search(line) for p in _ERROR_PATTERNS)
        else SignalKind.NORMAL
    )


# ── Thread-safe state shared between stream readers and watchdog ───────────


@dataclass(frozen=True)
class WatchdogSnapshot:
    """Immutable point-in-time copy of :class:`WatchdogState`.

    Returned by :meth:`WatchdogState.snapshot` so the watchdog loop reads a
    consistent set of values without holding the lock across I/O.
    """

    last_line_time: float
    consecutive_errors: int
    last_error_time: float
    last_error_message: str
    total_lines: int


class WatchdogState:
    """Thread-safe activity state updated by stream readers, read by watchdog.

    A single instance is shared between the stdout reader thread, the stderr
    reader thread, and the watchdog thread. All mutations go through the
    internal lock.
    """

    def __init__(self, start_time: float) -> None:
        self._start_time = start_time
        self._last_line_time: float = start_time
        self._consecutive_errors: int = 0
        self._last_error_time: float = 0.0
        self._last_error_message: str = ""
        self._total_lines: int = 0
        self._lock = threading.Lock()

    def record_line(self, line: str) -> None:
        """Record a line received from the process stdout/stderr.

        Called by :func:`webhook_receiver.runner._stream_to_logger_and_file`
        for every line. Updates timestamps, error counters, and line count
        under the lock.
        """
        kind = classify_line(line)
        now = time.monotonic()
        with self._lock:
            self._last_line_time = now
            self._total_lines += 1
            if kind is SignalKind.ERROR:
                self._consecutive_errors += 1
                self._last_error_time = now
                self._last_error_message = line.strip()[:200]
            else:
                self._consecutive_errors = 0

    def snapshot(self) -> WatchdogSnapshot:
        """Return an immutable copy of the current state."""
        with self._lock:
            return WatchdogSnapshot(
                last_line_time=self._last_line_time,
                consecutive_errors=self._consecutive_errors,
                last_error_time=self._last_error_time,
                last_error_message=self._last_error_message,
                total_lines=self._total_lines,
            )

    @property
    def start_time(self) -> float:
        return self._start_time


# ── Watchdog configuration ─────────────────────────────────────────────────


@dataclass(frozen=True)
class WatchdogConfig:
    """Tunables for :class:`IdleWatchdog`.

    Mirrors the constants from the old system's ``run_opencode_prompt.sh`` with
    the same proven values: 15-min idle timeout, 90-min hard ceiling, 30-s
    poll interval.
    """

    idle_timeout_secs: int = 900
    error_grace_secs: int = 300
    hard_ceiling_secs: int | None = 5400
    poll_interval_secs: int = 30
    max_consecutive_errors: int = 5
    sigterm_grace_secs: int = 10
    debug: bool = False

    @classmethod
    def from_settings(cls, settings: object) -> WatchdogConfig:
        """Build from a :class:`webhook_receiver.config.Settings` instance.

        Accepts ``object`` to avoid a circular import (config imports nothing
        from this module, but this module would otherwise need to import
        config just for type hints).
        """
        return cls(
            idle_timeout_secs=getattr(settings, "idle_timeout_secs", 900),
            error_grace_secs=getattr(settings, "error_grace_secs", 300),
            hard_ceiling_secs=getattr(settings, "hard_ceiling_secs", 5400),
            poll_interval_secs=getattr(settings, "watchdog_poll_secs", 30),
            max_consecutive_errors=getattr(settings, "max_consecutive_errors", 5),
            debug=getattr(settings, "watchdog_debug", False),
        )


# ── Watchdog result ────────────────────────────────────────────────────────

#: Sentinel for a process that exited on its own (watchdog did not kill it).
REASON_PROCESS_EXIT = "process_exit"
REASON_IDLE_TIMEOUT = "idle_timeout"
REASON_HARD_CEILING = "hard_ceiling"
REASON_CONSECUTIVE_ERRORS = "consecutive_errors"


@dataclass(frozen=True)
class WatchdogResult:
    """Outcome of a watchdog run.

    ``killed`` is ``True`` when the watchdog terminated the process (idle,
    consecutive errors, or hard ceiling). ``False`` means the process exited
    on its own.
    """

    killed: bool
    reason: str
    elapsed: float
    exit_code: int | None = None
    consecutive_errors: int = 0
    last_error_message: str = ""
    last_line_time: float = 0.0
    total_lines: int = 0


# ── Idle watchdog ──────────────────────────────────────────────────────────


class IdleWatchdog:
    """Monitors a subprocess for activity, killing it if it goes idle.

    The watchdog runs in a dedicated thread (started by
    :func:`webhook_receiver.runner._run_completion_watcher`). It polls
    :class:`WatchdogState` every ``poll_interval_secs`` and kills the process
    when a kill condition is met.

    Live heartbeat tracing is emitted as ``[watchdog]`` log lines so a
    post-mortem can reconstruct the run's activity timeline without debug mode.
    """

    def __init__(
        self,
        proc: subprocess.Popen,
        state: WatchdogState,
        config: WatchdogConfig,
        stderr_path: Path | None = None,
    ) -> None:
        self._proc = proc
        self._state = state
        self._config = config
        self._stderr_path = stderr_path

    def run(self) -> WatchdogResult:
        """Main watchdog loop. Blocks until the process exits or is killed.

        Returns a :class:`WatchdogResult` describing what happened. The caller
        (:func:`webhook_receiver.runner._run_completion_watcher`) uses this to
        classify the run and post the appropriate failure comment.
        """
        cfg = self._config
        start = self._state.start_time

        while True:
            # Check if the process has already exited.
            exit_code = self._proc.poll()
            if exit_code is not None:
                elapsed = time.monotonic() - start
                snap = self._state.snapshot()
                logger.info(
                    "[watchdog] process exited on its own "
                    "elapsed=%ds exit_code=%s lines=%d",
                    int(elapsed),
                    exit_code,
                    snap.total_lines,
                )
                return WatchdogResult(
                    killed=False,
                    reason=REASON_PROCESS_EXIT,
                    elapsed=elapsed,
                    exit_code=exit_code,
                    consecutive_errors=snap.consecutive_errors,
                    last_error_message=snap.last_error_message,
                    last_line_time=snap.last_line_time,
                    total_lines=snap.total_lines,
                )

            now = time.monotonic()
            elapsed = now - start
            snap = self._state.snapshot()
            line_idle = now - snap.last_line_time

            # ── 1. Hard ceiling (unconditional) ────────────────────────────
            if cfg.hard_ceiling_secs is not None and elapsed >= cfg.hard_ceiling_secs:
                logger.warning(
                    "[watchdog] HARD CEILING reached elapsed=%ds ceiling=%ds "
                    "lines=%d — terminating",
                    int(elapsed),
                    cfg.hard_ceiling_secs,
                    snap.total_lines,
                )
                self._dump_diagnostics(snap, REASON_HARD_CEILING)
                exit_code = self._terminate(REASON_HARD_CEILING)
                return WatchdogResult(
                    killed=True,
                    reason=REASON_HARD_CEILING,
                    elapsed=elapsed,
                    exit_code=exit_code,
                    consecutive_errors=snap.consecutive_errors,
                    last_error_message=snap.last_error_message,
                    last_line_time=snap.last_line_time,
                    total_lines=snap.total_lines,
                )

            # ── 2. Consecutive error abort ────────────────────────────────
            if snap.consecutive_errors >= cfg.max_consecutive_errors:
                error_idle = now - snap.last_error_time
                # Only fire if the most recent error is recent (within the
                # error grace window). This prevents a stale error burst from
                # killing a run that has since recovered.
                if error_idle <= cfg.error_grace_secs:
                    logger.warning(
                        "[watchdog] CONSECUTIVE ERRORS abort "
                        "errors=%d/%d error_idle=%ds grace=%ds "
                        "last_error=%r — terminating",
                        snap.consecutive_errors,
                        cfg.max_consecutive_errors,
                        int(error_idle),
                        cfg.error_grace_secs,
                        snap.last_error_message[:100],
                    )
                    self._dump_diagnostics(snap, REASON_CONSECUTIVE_ERRORS)
                    exit_code = self._terminate(REASON_CONSECUTIVE_ERRORS)
                    return WatchdogResult(
                        killed=True,
                        reason=REASON_CONSECUTIVE_ERRORS,
                        elapsed=elapsed,
                        exit_code=exit_code,
                        consecutive_errors=snap.consecutive_errors,
                        last_error_message=snap.last_error_message,
                        last_line_time=snap.last_line_time,
                        total_lines=snap.total_lines,
                    )

            # ── 3. Idle timeout ───────────────────────────────────────────
            if line_idle >= cfg.idle_timeout_secs:
                logger.warning(
                    "[watchdog] IDLE TIMEOUT line_idle=%ds threshold=%ds "
                    "elapsed=%ds lines=%d — terminating",
                    int(line_idle),
                    cfg.idle_timeout_secs,
                    int(elapsed),
                    snap.total_lines,
                )
                self._dump_diagnostics(snap, REASON_IDLE_TIMEOUT)
                exit_code = self._terminate(REASON_IDLE_TIMEOUT)
                return WatchdogResult(
                    killed=True,
                    reason=REASON_IDLE_TIMEOUT,
                    elapsed=elapsed,
                    exit_code=exit_code,
                    consecutive_errors=snap.consecutive_errors,
                    last_error_message=snap.last_error_message,
                    last_line_time=snap.last_line_time,
                    total_lines=snap.total_lines,
                )

            # ── Heartbeat trace ───────────────────────────────────────────
            # Always emit a heartbeat when output has been silent for >= 60s
            # (so a slow-but-working run is visible), or on every poll when
            # debug mode is on.
            if line_idle >= 60 or cfg.debug:
                logger.info(
                    "[watchdog] elapsed=%ds line_idle=%ds errors=%d/%d "
                    "lines=%d pid=%s",
                    int(elapsed),
                    int(line_idle),
                    snap.consecutive_errors,
                    cfg.max_consecutive_errors,
                    snap.total_lines,
                    self._proc.pid,
                )

            # Sleep until the next poll. Use a short sleep so we catch a
            # process exit quickly even when poll_interval is large.
            slept = 0.0
            while slept < cfg.poll_interval_secs:
                step = min(1.0, cfg.poll_interval_secs - slept)
                time.sleep(step)
                slept += step
                if self._proc.poll() is not None:
                    break  # process exited during sleep — re-check immediately

    # ── Termination and diagnostics ───────────────────────────────────────

    def _terminate(self, reason: str) -> int:
        """SIGTERM → grace → SIGKILL escalation, returning the exit code.

        Mirrors the old system's proven sequence: send SIGTERM, wait up to
        ``sigterm_grace_secs`` for graceful exit, then SIGKILL if still alive.
        """
        proc = self._proc
        try:
            proc.terminate()  # SIGTERM
        except ProcessLookupError:
            # Already gone — race with natural exit.
            pass

        try:
            proc.wait(timeout=self._config.sigterm_grace_secs)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[watchdog] process did not exit after SIGTERM "
                "(grace=%ds), sending SIGKILL",
                self._config.sigterm_grace_secs,
            )
            try:
                proc.kill()  # SIGKILL
            except ProcessLookupError:
                pass
            proc.wait()

        return proc.returncode if proc.returncode is not None else -15

    def _dump_diagnostics(self, snap: WatchdogSnapshot, reason: str) -> None:
        """Dump recent stderr lines + state summary before termination.

        Critical for post-mortem: shows what the process was doing in the
        minutes before the kill. Mirrors the old system's "server log tail"
        dump.
        """
        logger.warning(
            "[watchdog] pre-termination diagnostics reason=%s "
            "elapsed=%ds consecutive_errors=%d total_lines=%d "
            "last_error=%r",
            reason,
            int(time.monotonic() - self._state.start_time),
            snap.consecutive_errors,
            snap.total_lines,
            snap.last_error_message[:120],
        )

        if self._stderr_path is None or not self._stderr_path.exists():
            return

        try:
            lines = self._stderr_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return

        # Show the last 20 lines (the "what was it doing" window).
        tail = lines[-20:] if len(lines) > 20 else lines
        if tail:
            logger.warning("[watchdog] === recent stderr (last %d lines) ===", len(tail))
            for line in tail:
                logger.warning("[watchdog]   %s", line.rstrip())
            logger.warning("[watchdog] === end recent stderr ===")
