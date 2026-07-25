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

import base64
import logging
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
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
    return SignalKind.ERROR if any(p.search(line) for p in _ERROR_PATTERNS) else SignalKind.NORMAL


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


# ── Server-log activity monitor (scoped to the current dispatch) ────────────


class _ServerLogMonitor:
    """Tracks server-log growth scoped to a single dispatch.

    The opencode server log (``/home/app/.local/share/opencode/log/opencode.log``)
    is a SINGLE shared file written by every concurrent dispatch attaching to
    the same server. A bare ``st_mtime`` check is therefore a *global* signal:
    one busy session keeps ``server_log_idle`` near zero for every other
    session, masking a genuinely stuck run until the hard ceiling fires.

    This monitor scopes the signal to the current dispatch by treating the log
    as active ONLY while it is **actively growing** — i.e. new bytes were
    appended since the previous poll, beyond the byte offset present at this
    dispatch's start. A log that was last written before the dispatch (or that
    stopped growing) does NOT reset the idle clock, so a stuck run whose own
    server is silent is still killed on idle. Only true ongoing server writes
    (the subagent-delegation case the signal exists for) withhold the kill.

    Residual limitation: two dispatches running truly concurrently against one
    server share one growing file, so one actively-writing session can still
    briefly mask another. That window is bounded by the hard ceiling and by the
    fact that no single session writes continuously forever; full per-session
    isolation would require opencode to emit per-session log markers.
    """

    def __init__(self, path: str, start_time: float) -> None:
        self._path: Path | None = Path(path) if path else None
        # Baseline byte offset at dispatch start: pre-existing content does not
        # count as activity for this run.
        self._pos = self._size_or_zero()
        self._last_growth = start_time

    def _size_or_zero(self) -> int:
        try:
            return self._path.stat().st_size if self._path is not None else 0
        except OSError:
            return 0  # missing/unreadable — treat as empty baseline

    def idle_secs(self, now: float) -> float | None:
        """Seconds since the server log last grew, or ``None`` if disabled.

        ``None`` (disabled path empty, or file missing/inaccessible) makes the
        caller fall back to client-only monitoring. A finite value is the
        time since the most recent byte growth beyond this dispatch's baseline.
        """
        if self._path is None:
            return None
        try:
            size = self._path.stat().st_size
        except OSError:
            return None
        if size > self._pos:
            self._pos = size
            self._last_growth = now
        return now - self._last_growth


class _PermissionAskMonitor:
    """Detects unanswered permission ``ask`` prompts in the opencode server log.

    A headless dispatch (``--auto``, no human responder)
    can NEVER satisfy a permission ``ask``: the agent blocks forever waiting for
    a reply. opencode emits ``message=asking ... permission=<type>`` lines to its
    server log when ``evaluate()`` resolves a tool call to ``ask`` (distinct from
    ``message=evaluated``, which fires for allow/deny too). This scanner reads
    only the bytes appended to the shared server log *during this dispatch* and
    records the most recent unanswered ask. The watchdog then kills the run once
    that ask has aged past ``permission_ask_grace_secs`` instead of hanging until
    the much longer idle ceiling fires.

    Defensive: a subsequent ``message=replied`` clears the pending ask, in case
    opencode resolves it via a saved "always" approval — though this cannot
    happen for subagents in skip-permissions mode.
    """

    # opencode slog text format, e.g.:
    #   ... message=asking id=per_.. permission=external_directory patterns=[..]
    _ASK_RE = re.compile(r"message=asking\b.*?permission=([A-Za-z_]+)")
    _REPLIED_RE = re.compile(r"message=replied|permission\.replied")
    # The dispatch's opencode session id, e.g. ``session.id=ses_abc123``. The
    # FIRST one seen in this dispatch's log window is locked in — the session
    # is created at run start, so the earliest match is this dispatch's own
    # (resilient to concurrent-dispatch noise sharing the one log file).
    _SESSION_RE = re.compile(r"session\.id=(ses_[A-Za-z0-9]+)")

    def __init__(self, path: str) -> None:
        self._path: Path | None = Path(path) if path else None
        self._pos = self._size_or_zero()
        self._last_ask_time: float | None = None
        self._last_ask_detail: str = ""
        self._session_id: str | None = None

    def _size_or_zero(self) -> int:
        try:
            return self._path.stat().st_size if self._path is not None else 0
        except OSError:
            return 0

    def poll(self, now: float) -> None:
        """Consume new server-log bytes; track the latest unanswered ``ask``."""
        if self._path is None:
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        # Log rotation/truncation: reset to head so we don't miss a fresh ask.
        if size < self._pos:
            self._pos = 0
        if size <= self._pos:
            return
        try:
            with self._path.open("rb") as fh:
                fh.seek(self._pos)
                chunk = fh.read(size - self._pos)
        except OSError:
            return  # unreadable this cycle; retry next poll
        self._pos = size
        text = chunk.decode("utf-8", errors="replace")
        # Capture this dispatch's session id (first-seen wins) so the watchdog
        # can abort the server-side session on termination.
        if self._session_id is None:
            sm = self._SESSION_RE.search(text)
            if sm:
                self._session_id = sm.group(1)
        if self._REPLIED_RE.search(text):
            # An ask in flight was resolved — no longer deadlocked.
            self._last_ask_time = None
            return
        last_match = None
        for match in self._ASK_RE.finditer(text):
            last_match = match
        if last_match is not None:
            self._last_ask_time = now
            self._last_ask_detail = last_match.group(0).strip()[:160]

    @property
    def pending_ask(self) -> tuple[float, str] | None:
        """``(ask_monotonic_time, detail)`` if an unanswered ask is pending."""
        if self._last_ask_time is None:
            return None
        return (self._last_ask_time, self._last_ask_detail)

    @property
    def session_id(self) -> str | None:
        """The dispatch's opencode session id, once seen in the server log."""
        return self._session_id


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
    # Grace window before an unanswered permission `ask` (detected in the server
    # log) is treated as a fatal headless deadlock and the run is killed. In a
    # headless dispatch no `ask` can ever be answered, so this is kept short;
    # the window only absorbs a transiently-logged ask that opencode resolves
    # via a saved "always" approval (rare; impossible for skip-perms subagents).
    permission_ask_grace_secs: int = 60
    # Path to the opencode server's log file (shared via the opencode-logs
    # volume). The watchdog treats the log as a per-dispatch activity signal by
    # tracking byte growth since the run started: ongoing appends (subagent
    # delegation) withhold an idle kill, while a non-growing log falls back to
    # client-only monitoring. Empty string disables the signal.
    server_log_path: str = "/home/app/.local/share/opencode/log/opencode.log"
    # opencode server connection for server-side session abort on termination.
    # The client process-group kill only reaps the local opencode-run client;
    # the session lives in the (separate-container) server and would otherwise
    # orphan — blocked on an unanswered ask or an active agent loop. On any
    # watchdog trip the captured session id is POST-aborted via this URL.
    # Empty server_url disables the abort (the client kill still happens).
    server_url: str = ""
    server_username: str = "opencode"
    server_password: str = ""

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
            permission_ask_grace_secs=getattr(settings, "permission_ask_grace_secs", 60),
            server_log_path=getattr(
                settings,
                "server_log_path",
                "/home/app/.local/share/opencode/log/opencode.log",
            ),
            server_url=getattr(settings, "opencode_server_url", ""),
            server_username=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
            server_password=os.environ.get("OPENCODE_SERVER_PASSWORD", ""),
        )


# ── Watchdog result ────────────────────────────────────────────────────────

#: Sentinel for a process that exited on its own (watchdog did not kill it).
REASON_PROCESS_EXIT = "process_exit"
REASON_IDLE_TIMEOUT = "idle_timeout"
REASON_HARD_CEILING = "hard_ceiling"
REASON_CONSECUTIVE_ERRORS = "consecutive_errors"
# An unanswered permission `ask` detected in the server log. In headless
# dispatches (no human to answer) this is an unrecoverable deadlock.
REASON_PERMISSION_DEADLOCK = "permission_deadlock"


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
        # Server-log growth monitor scoped to THIS dispatch (see
        # _ServerLogMonitor). Baseline is captured at watchdog start so
        # pre-existing log content can't mask a stuck run.
        self._server_monitor = _ServerLogMonitor(config.server_log_path, state.start_time)
        # Permission-ask deadlock scanner (headless fail-fast). Shares the same
        # server-log path as the growth monitor but tracks its own read offset.
        self._ask_monitor = _PermissionAskMonitor(config.server_log_path)

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
                    "[watchdog] process exited on its own elapsed=%ds exit_code=%s lines=%d",
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

            # ── Server-log growth activity signal (dispatch-scoped) ───────
            # The opencode server (running in a separate container) writes
            # structured log entries during agent/subagent execution. When the
            # orchestrator delegates to a subagent, the client stdout goes
            # silent (the client blocks waiting for the server-side subagent),
            # but the server log grows. Checking whether the log has grown
            # since the last poll gives the watchdog a per-dispatch activity
            # signal that prevents false-positive idle kills during subagent
            # delegation — without the global-mtime masking problem (a busy
            # unrelated session only counts while it is actively appending).
            server_log_idle: float | None = self._server_monitor.idle_secs(now)

            # Effective idle = whichever signal is MORE RECENT (lower idle).
            # If either the client stdout OR the server log shows recent
            # activity, the process is not idle.
            if server_log_idle is not None:
                effective_idle = min(line_idle, server_log_idle)
            else:
                effective_idle = line_idle

            # ── Permission-ask deadlock scan (headless fail-fast) ─────────
            # Read newly-appended server-log bytes and track any unanswered
            # permission `ask`. The kill decision based on this is below.
            self._ask_monitor.poll(now)

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

            # ── 3. Permission-ask deadlock (headless fail-fast) ──────────
            # In headless mode an unanswered permission `ask` can never be
            # resolved; once one ages past the grace window, kill the run
            # immediately rather than waiting for the idle ceiling (which can
            # be 15+ minutes). This is the deterministic signature of the
            # subagent external_directory deadlock (opencode #30527 cluster).
            ask_pending = self._ask_monitor.pending_ask
            if ask_pending is not None:
                ask_age = now - ask_pending[0]
                if ask_age >= cfg.permission_ask_grace_secs:
                    logger.warning(
                        "[watchdog] PERMISSION DEADLOCK unanswered ask "
                        "ask_age=%ds grace=%ds detail=%r "
                        "elapsed=%ds lines=%d — terminating",
                        int(ask_age),
                        cfg.permission_ask_grace_secs,
                        ask_pending[1][:120],
                        int(elapsed),
                        snap.total_lines,
                    )
                    self._dump_diagnostics(snap, REASON_PERMISSION_DEADLOCK)
                    exit_code = self._terminate(REASON_PERMISSION_DEADLOCK)
                    return WatchdogResult(
                        killed=True,
                        reason=REASON_PERMISSION_DEADLOCK,
                        elapsed=elapsed,
                        exit_code=exit_code,
                        consecutive_errors=snap.consecutive_errors,
                        last_error_message=snap.last_error_message,
                        last_line_time=snap.last_line_time,
                        total_lines=snap.total_lines,
                    )

            # ── 4. Idle timeout ───────────────────────────────────────────
            # Uses effective_idle (min of client line_idle and server log
            # mtime idle) so a subagent actively working on the server does
            # not trigger a false-positive kill.
            if effective_idle >= cfg.idle_timeout_secs:
                logger.warning(
                    "[watchdog] IDLE TIMEOUT line_idle=%ds server_log_idle=%s "
                    "effective_idle=%ds threshold=%ds "
                    "elapsed=%ds lines=%d — terminating",
                    int(line_idle),
                    f"{int(server_log_idle)}s" if server_log_idle is not None else "n/a",
                    int(effective_idle),
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
                _sl_idle_str = f"{int(server_log_idle)}s" if server_log_idle is not None else "n/a"
                # When the server log is active but the client is idle, note
                # that a subagent is likely running (the server is doing work
                # that the client can't stream).
                if (
                    server_log_idle is not None
                    and server_log_idle < cfg.poll_interval_secs
                    and line_idle >= cfg.poll_interval_secs
                ):
                    _note = " — server active (subagent likely running)"
                else:
                    _note = ""
                logger.info(
                    "[watchdog] elapsed=%ds line_idle=%ds server_log_idle=%s "
                    "effective_idle=%ds errors=%d/%d "
                    "lines=%d pid=%s%s",
                    int(elapsed),
                    int(line_idle),
                    _sl_idle_str,
                    int(effective_idle),
                    snap.consecutive_errors,
                    cfg.max_consecutive_errors,
                    snap.total_lines,
                    self._proc.pid,
                    _note,
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
        """Abort the server-side session, then SIGTERM → grace → SIGKILL the
        client process group.

        Two independent cleanup targets, because client and server run in
        separate containers:

        1. **Server session abort** — ``POST {server}/session/{id}/abort``.
           This is the precise way to stop the server-side agent loop. Without
           it the session orphans (the client kill alone cannot reach the
           server), staying blocked on an unanswered permission ask or an
           active subagent until the server is restarted. Best-effort: a
           network failure does not block the client kill below.

        2. **Client process-group kill** — ``os.killpg`` escalation. The runner
           spawns opencode with ``start_new_session=True``, making the child a
           session/process-group leader. Signalling the *group* (not just the
           direct child PID) ensures grandchild processes (e.g. ``gh`` CLI
           calls spawned by subagents) are also terminated, preventing orphaned
           processes from continuing to mutate GitHub state after the kill.
        """
        # Server-side session abort FIRST: cleanly stops the agent loop so the
        # client can exit on its own; the killpg below is then hard cleanup.
        self._abort_server_session()

        proc = self._proc
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = proc.pid  # already gone — fall back to direct PID

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # already gone — race with natural exit

        try:
            proc.wait(timeout=self._config.sigterm_grace_secs)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[watchdog] process group did not exit after SIGTERM (grace=%ds), sending SIGKILL",
                self._config.sigterm_grace_secs,
            )
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

        return proc.returncode if proc.returncode is not None else -15

    def _abort_server_session(self) -> None:
        """Best-effort ``POST {server}/session/{id}/abort``.

        Captured session id comes from the server log via
        :attr:`_PermissionAskMonitor.session_id`. Skipped silently when the
        server URL or session id is unavailable (the client kill still
        proceeds). Never raises — this is cleanup, not a control path.
        """
        cfg = self._config
        session_id = self._ask_monitor.session_id
        if not cfg.server_url or not session_id:
            logger.info(
                "[watchdog] server-session abort skipped (server_url=%r session_id=%r)",
                cfg.server_url,
                session_id,
            )
            return
        url = f"{cfg.server_url.rstrip('/')}/session/{session_id}/abort"
        req = urllib.request.Request(url, method="POST", data=b"")
        if cfg.server_password:
            token = base64.b64encode(
                f"{cfg.server_username}:{cfg.server_password}".encode()
            ).decode()
            req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                logger.warning(
                    "[watchdog] server session aborted url=%s status=%s",
                    url,
                    resp.status,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
            logger.warning(
                "[watchdog] server session abort FAILED (best-effort; "
                "client kill still proceeds) url=%s err=%s",
                url,
                exc,
            )

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
            lines = self._stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return

        # Show the last 20 lines (the "what was it doing" window).
        tail = lines[-20:] if len(lines) > 20 else lines
        if tail:
            logger.warning("[watchdog] === recent stderr (last %d lines) ===", len(tail))
            for line in tail:
                logger.warning("[watchdog]   %s", line.rstrip())
            logger.warning("[watchdog] === end recent stderr ===")
