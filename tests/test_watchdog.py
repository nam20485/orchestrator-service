"""Unit tests for the idle watchdog module.

Covers:
1. Signal classification (classify_line): error vs normal patterns
2. WatchdogState: thread-safe record_line / snapshot
3. IdleWatchdog: idle timeout, consecutive errors, hard ceiling, process exit
4. WatchdogConfig.from_settings
"""

from __future__ import annotations

import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from webhook_receiver.watchdog import (
    REASON_CONSECUTIVE_ERRORS,
    REASON_HARD_CEILING,
    REASON_IDLE_TIMEOUT,
    REASON_PERMISSION_DEADLOCK,
    REASON_PROCESS_EXIT,
    IdleWatchdog,
    SignalKind,
    WatchdogConfig,
    WatchdogState,
    _PermissionAskMonitor,
    _ServerLogMonitor,
    classify_line,
)

# ── Signal classification ─────────────────────────────────────────────────


class TestClassifyLine:
    def test_normal_line_is_normal(self) -> None:
        assert classify_line("⚙ bash some command") is SignalKind.NORMAL
        assert classify_line("• Create plan issue Planner Agent") is SignalKind.NORMAL
        assert classify_line("just some text output") is SignalKind.NORMAL

    def test_level_error_is_error(self) -> None:
        assert (
            classify_line('timestamp=2026-07-11 level=ERROR run=70c74cd7 message="stream error"')
            is SignalKind.ERROR
        )

    def test_ai_api_call_error_is_error(self) -> None:
        assert (
            classify_line('error.error="AI_APICallError: Usage limit reached for 5 hour"')
            is SignalKind.ERROR
        )

    def test_usage_limit_reached_is_error(self) -> None:
        assert classify_line("Usage limit reached for 5 hour") is SignalKind.ERROR

    def test_rate_limit_is_error(self) -> None:
        assert classify_line("Rate limit exceeded") is SignalKind.ERROR

    def test_error_prefix_is_error(self) -> None:
        assert classify_line("Error: something went wrong") is SignalKind.ERROR
        assert classify_line("  Error: indented error") is SignalKind.ERROR

    def test_error_glyph_is_error(self) -> None:
        assert classify_line("✗ memory_add_observations failed") is SignalKind.ERROR

    def test_failed_keyword_is_error(self) -> None:
        assert classify_line("operation failed") is SignalKind.ERROR

    def test_blank_line_is_normal(self) -> None:
        assert classify_line("") is SignalKind.NORMAL
        assert classify_line("\n") is SignalKind.NORMAL


# ── WatchdogState ──────────────────────────────────────────────────────────


class TestWatchdogState:
    def test_initial_state(self) -> None:
        start = time.monotonic()
        state = WatchdogState(start)
        snap = state.snapshot()
        assert snap.last_line_time == start
        assert snap.consecutive_errors == 0
        assert snap.last_error_time == 0.0
        assert snap.last_error_message == ""
        assert snap.total_lines == 0

    def test_record_normal_line(self) -> None:
        state = WatchdogState(time.monotonic())
        state.record_line("⚙ bash echo hello")
        snap = state.snapshot()
        assert snap.total_lines == 1
        assert snap.consecutive_errors == 0

    def test_record_error_line(self) -> None:
        state = WatchdogState(time.monotonic())
        state.record_line("level=ERROR something broke")
        snap = state.snapshot()
        assert snap.total_lines == 1
        assert snap.consecutive_errors == 1
        assert "something broke" in snap.last_error_message

    def test_consecutive_errors_increment(self) -> None:
        state = WatchdogState(time.monotonic())
        for i in range(5):
            state.record_line(f"level=ERROR error {i}")
        snap = state.snapshot()
        assert snap.consecutive_errors == 5
        assert snap.total_lines == 5

    def test_normal_line_resets_consecutive_errors(self) -> None:
        state = WatchdogState(time.monotonic())
        state.record_line("level=ERROR error 1")
        state.record_line("level=ERROR error 2")
        assert state.snapshot().consecutive_errors == 2
        state.record_line("⚙ bash echo recovered")
        assert state.snapshot().consecutive_errors == 0

    def test_error_message_truncated(self) -> None:
        state = WatchdogState(time.monotonic())
        long_msg = "Error: " + "x" * 500
        state.record_line(long_msg)
        snap = state.snapshot()
        assert len(snap.last_error_message) <= 200

    def test_snapshot_is_immutable_copy(self) -> None:
        state = WatchdogState(time.monotonic())
        state.record_line("line 1")
        snap1 = state.snapshot()
        state.record_line("line 2")
        snap2 = state.snapshot()
        assert snap1.total_lines == 1
        assert snap2.total_lines == 2  # snap1 unchanged


# ── WatchdogConfig ─────────────────────────────────────────────────────────


class TestWatchdogConfig:
    def test_defaults(self) -> None:
        cfg = WatchdogConfig()
        assert cfg.idle_timeout_secs == 900
        assert cfg.hard_ceiling_secs == 5400
        assert cfg.poll_interval_secs == 30
        assert cfg.max_consecutive_errors == 5
        assert cfg.debug is False
        assert cfg.permission_ask_grace_secs == 60

    def test_from_settings(self) -> None:
        settings = MagicMock()
        settings.idle_timeout_secs = 600
        settings.error_grace_secs = 120
        settings.hard_ceiling_secs = 3600
        settings.watchdog_poll_secs = 15
        settings.max_consecutive_errors = 3
        settings.watchdog_debug = True
        cfg = WatchdogConfig.from_settings(settings)
        assert cfg.idle_timeout_secs == 600
        assert cfg.error_grace_secs == 120
        assert cfg.hard_ceiling_secs == 3600
        assert cfg.poll_interval_secs == 15
        assert cfg.max_consecutive_errors == 3
        assert cfg.debug is True

    def test_from_settings_missing_attrs_uses_defaults(self) -> None:
        """When settings object lacks watchdog attrs, defaults apply."""
        cfg = WatchdogConfig.from_settings(object())
        assert cfg.idle_timeout_secs == 900
        assert cfg.hard_ceiling_secs == 5400
        assert cfg.permission_ask_grace_secs == 60


# ── IdleWatchdog ───────────────────────────────────────────────────────────


def _mock_proc(returncode: int | None = None) -> MagicMock:
    """A mock subprocess.Popen suitable for IdleWatchdog testing.

    *returncode* controls poll() behavior:
    - ``None`` → process still running (poll returns None)
    - int → process exited (poll returns the code)
    """
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode

    if returncode is None:
        proc.poll.return_value = None
    else:
        proc.poll.return_value = returncode

    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = MagicMock()
    return proc


class TestIdleWatchdogProcessExit:
    def test_process_exits_on_own(self) -> None:
        """When the process exits before any kill condition, the watchdog
        returns REASON_PROCESS_EXIT without killing."""
        proc = _mock_proc(returncode=0)
        state = WatchdogState(time.monotonic())
        cfg = WatchdogConfig(poll_interval_secs=0)
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is False
        assert result.reason == REASON_PROCESS_EXIT
        assert result.exit_code == 0
        proc.terminate.assert_not_called()


class TestIdleWatchdogIdleTimeout:
    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_idle_timeout_kills_process(
        self, mock_getpgid: MagicMock, mock_killpg: MagicMock
    ) -> None:
        """When no lines arrive for IDLE_TIMEOUT_SECS, the watchdog kills."""
        proc = _mock_proc(returncode=None)
        # After killpg(), poll() returns -15 (SIGTERM).
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345
        state = WatchdogState(time.monotonic() - 1000)  # 1000s ago = already idle
        # Set last_line_time far in the past so line_idle > idle_timeout.
        state._last_line_time = time.monotonic() - 1000
        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path="",  # disable server-log signal
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_IDLE_TIMEOUT
        mock_killpg.assert_called_once_with(12345, signal.SIGTERM)


class TestIdleWatchdogHardCeiling:
    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_hard_ceiling_kills_regardless_of_activity(
        self, mock_getpgid: MagicMock, mock_killpg: MagicMock
    ) -> None:
        """Even with recent activity, the hard ceiling fires unconditionally."""
        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 100)
        state.record_line("recent line")  # not idle

        cfg = WatchdogConfig(
            idle_timeout_secs=999999,  # won't fire
            hard_ceiling_secs=1,  # fires immediately (elapsed=100)
            poll_interval_secs=0,
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_HARD_CEILING


class TestIdleWatchdogConsecutiveErrors:
    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_consecutive_errors_kill_process(
        self, mock_getpgid: MagicMock, mock_killpg: MagicMock
    ) -> None:
        """When MAX_CONSECUTIVE_ERRORS error lines arrive, the watchdog kills."""
        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic())
        # Simulate 5 consecutive error lines.
        for i in range(5):
            state.record_line(f"level=ERROR error {i}")

        cfg = WatchdogConfig(
            idle_timeout_secs=999999,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            max_consecutive_errors=5,
            error_grace_secs=300,
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_CONSECUTIVE_ERRORS
        assert result.consecutive_errors == 5

    def test_stale_error_burst_does_not_kill(self) -> None:
        """Errors outside the grace window don't fire (recovered since)."""
        proc = _mock_proc(returncode=0)

        state = WatchdogState(time.monotonic())
        # Simulate 5 errors long ago.
        for i in range(5):
            state.record_line(f"level=ERROR error {i}")
        # Push last_error_time into the past beyond grace.
        state._last_error_time = time.monotonic() - 600

        cfg = WatchdogConfig(
            idle_timeout_secs=999999,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            max_consecutive_errors=5,
            error_grace_secs=300,  # 5 min — errors are 10 min old
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        # The stale errors don't fire; the process exits on its own.
        assert result.killed is False
        assert result.reason == REASON_PROCESS_EXIT


class TestIdleWatchdogTermination:
    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_sigterm_then_sigkill_on_timeout(
        self, mock_getpgid: MagicMock, mock_killpg: MagicMock
    ) -> None:
        """When SIGTERM doesn't cause exit within grace, SIGKILL is sent."""
        proc = _mock_proc(returncode=None)
        proc.pid = 999
        mock_getpgid.return_value = 999

        # poll() sequence: running, then after terminate → still running,
        # then after kill → -9.
        proc.poll.side_effect = [None, None, -9]
        # wait() raises TimeoutExpired first (SIGTERM didn't work), then returns.
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="x", timeout=1),
            None,
        ]

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000
        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            sigterm_grace_secs=0,
            server_log_path="",  # disable server-log signal
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        # SIGTERM sent first, then SIGKILL after grace timeout.
        calls = mock_killpg.call_args_list
        assert len(calls) == 2
        assert calls[0].args == (999, signal.SIGTERM)
        assert calls[1].args == (999, signal.SIGKILL)


class TestIdleWatchdogDiagnostics:
    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_stderr_dumped_on_kill(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
        caplog,
    ) -> None:
        """Pre-termination diagnostics dump recent stderr lines."""
        stderr_file = tmp_path / "test.stderr"
        stderr_file.write_text("line one\nline two\nline three\n", encoding="utf-8")

        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000
        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path="",  # disable server-log signal
        )
        wd = IdleWatchdog(proc, state, cfg, stderr_path=stderr_file)

        with caplog.at_level("WARNING"):
            wd.run()

        # The diagnostics should include the recent stderr lines.
        log_text = " ".join(r.getMessage() for r in caplog.records)
        assert "line three" in log_text


# ── Server-log growth activity signal (dispatch-scoped) ──────────────────


class TestServerLogActivitySignal:
    """Tests for the server-log mtime secondary activity signal.

    When the opencode client stdout goes silent during subagent delegation,
    the server log continues to grow. The watchdog checks whether the log has
    grown since the previous poll as a dispatch-scoped signal to avoid
    false-positive idle kills.
    """

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_server_log_active_withholds_kill(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """Server log actively growing → kill withheld even if client is idle.

        The growth signal is dispatch-scoped: a monitor reporting ongoing
        growth (subagent delegation) resets effective_idle to ~0 so the idle
        timeout does not fire, even with a long-silent client. Contrast with
        test_server_log_stale_allows_kill where growth stops → kill fires.
        """
        proc = _mock_proc(returncode=None)
        proc.returncode = 0
        # First poll: process running (idle check runs). Second poll: exits.
        proc.poll.side_effect = [None, 0]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000  # client idle 1000s

        cfg = WatchdogConfig(
            idle_timeout_secs=1,  # would kill on client idle alone
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path="/dev/null",  # path present; monitor overridden
        )
        wd = IdleWatchdog(proc, state, cfg)
        # Inject a monitor reporting continuous growth (active server writes).
        monitor = MagicMock()
        monitor.idle_secs.return_value = 0.0
        wd._server_monitor = monitor

        result = wd.run()

        # Growing server log → effective_idle ~0 < threshold → no idle kill.
        # Process then exits on its own.
        assert result.killed is False
        assert result.reason == REASON_PROCESS_EXIT
        mock_killpg.assert_not_called()

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_server_log_stale_allows_kill(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Both client AND server log stale → kill fires."""
        server_log = tmp_path / "opencode.log"
        server_log.write_text("old server entry\n", encoding="utf-8")
        # Set mtime far in the past.
        import os

        old_time = time.time() - 10000
        os.utime(server_log, (old_time, old_time))

        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000

        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path=str(server_log),
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_IDLE_TIMEOUT

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_server_log_missing_falls_back_to_client(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Server log file missing → falls back to client-only (current behavior)."""
        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000

        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path=str(tmp_path / "nonexistent.log"),
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        # File doesn't exist → server_log_idle is None → effective_idle = line_idle
        # → kill fires as before.
        assert result.killed is True
        assert result.reason == REASON_IDLE_TIMEOUT

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_server_log_disabled_falls_back_to_client(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """Empty server_log_path → signal disabled, client-only monitoring."""
        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000

        cfg = WatchdogConfig(
            idle_timeout_secs=1,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path="",  # disabled
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_IDLE_TIMEOUT


# ── _ServerLogMonitor: dispatch-scoped growth tracking ────────────────────


class TestServerLogMonitor:
    """Unit tests for the per-dispatch server-log growth monitor.

    The monitor converts the shared server log into a dispatch-scoped activity
    signal: the log is "active" only while new bytes are appended beyond the
    baseline present at this dispatch's start. This prevents a globally busy
    log (or a stale pre-dispatch log) from masking a genuinely stuck run.
    """

    def test_no_growth_accrues_idle(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        start = time.monotonic()
        mon = _ServerLogMonitor(str(log), start)
        # Same size on the next poll → no growth → idle accrues from start.
        assert mon.idle_secs(start + 5) == pytest.approx(5)
        assert mon.idle_secs(start + 12) == pytest.approx(12)

    def test_growth_resets_idle(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        start = time.monotonic()
        mon = _ServerLogMonitor(str(log), start)
        assert mon.idle_secs(start + 5) == pytest.approx(5)
        # Append bytes (server activity, e.g. subagent delegation).
        log.write_text("baseline\nnew server entry\n", encoding="utf-8")
        assert mon.idle_secs(start + 10) == 0  # grew → reset (exact)
        # No further growth → idle accrues from the last growth time.
        assert mon.idle_secs(start + 25) == pytest.approx(15)  # 25 - 10

    def test_pre_existing_content_excluded(self, tmp_path: Path) -> None:
        # Content present before the dispatch must NOT count as activity.
        log = tmp_path / "opencode.log"
        log.write_text("x" * 500, encoding="utf-8")
        start = time.monotonic()
        mon = _ServerLogMonitor(str(log), start)
        assert mon.idle_secs(start + 3) == pytest.approx(3)  # baseline excluded

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        mon = _ServerLogMonitor(str(tmp_path / "nope.log"), time.monotonic())
        assert mon.idle_secs(time.monotonic()) is None

    def test_file_disappears_mid_run_returns_none(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("x", encoding="utf-8")
        start = time.monotonic()
        mon = _ServerLogMonitor(str(log), start)
        log.unlink()  # file removed (e.g. log rotation) → fall back to client
        assert mon.idle_secs(start + 1) is None

    def test_empty_path_is_disabled(self) -> None:
        mon = _ServerLogMonitor("", time.monotonic())
        assert mon.idle_secs(time.monotonic()) is None


# ── _PermissionAskMonitor: headless permission-ask deadlock detection ──────


class TestPermissionAskMonitor:
    """Unit tests for the unanswered-permission-ask server-log scanner.

    In headless dispatches a permission ``ask`` can never be answered. opencode
    logs ``message=asking ... permission=<type>``; this monitor reads only bytes
    appended during the dispatch and tracks the latest unanswered ask.
    """

    def test_no_ask_is_none(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_detects_ask(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        now = time.monotonic()
        mon = _PermissionAskMonitor(str(log))
        with log.open("a", encoding="utf-8") as fh:
            fh.write(
                "timestamp=... level=INFO run=abc message=asking "
                'id=per_123 permission=external_directory patterns=["/tmp/x/*"]\n'
            )
        mon.poll(now)
        pending = mon.pending_ask
        assert pending is not None
        assert pending[0] == now
        assert "external_directory" in pending[1]

    def test_pre_existing_ask_excluded(self, tmp_path: Path) -> None:
        # An ask present BEFORE the dispatch (in the baseline) must not count.
        log = tmp_path / "opencode.log"
        log.write_text(
            'message=asking id=per_old permission=bash patterns=["*"]\n',
            encoding="utf-8",
        )
        mon = _PermissionAskMonitor(str(log))
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_replied_clears_pending_ask(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        with log.open("a", encoding="utf-8") as fh:
            fh.write("message=asking id=per_1 permission=external_directory patterns=[]\n")
        mon.poll(time.monotonic())
        assert mon.pending_ask is not None
        with log.open("a", encoding="utf-8") as fh:
            fh.write('timestamp=... message=replied reply="always"\n')
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_does_not_match_evaluated(self, tmp_path: Path) -> None:
        # `message=evaluated ... action.action=ask` is the evaluation log, not
        # the "prompting the user" signal — it must NOT be treated as a pending
        # ask (it fires even when the result is allow/deny).
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        with log.open("a", encoding="utf-8") as fh:
            fh.write(
                "message=evaluated permission=external_directory "
                "pattern=/tmp/x/* action.permission=external_directory "
                "action.action=ask action.pattern=*\n"
            )
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_empty_path_is_disabled(self) -> None:
        mon = _PermissionAskMonitor("")
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_missing_file_is_noop(self, tmp_path: Path) -> None:
        mon = _PermissionAskMonitor(str(tmp_path / "nope.log"))
        mon.poll(time.monotonic())
        assert mon.pending_ask is None

    def test_captures_session_id(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        with log.open("a", encoding="utf-8") as fh:
            fh.write(
                "timestamp=... level=INFO run=abc message=process "
                "session.id=ses_06aab8071ffeV8TotH696WH6Gi mode=primary\n"
            )
        mon.poll(time.monotonic())
        assert mon.session_id == "ses_06aab8071ffeV8TotH696WH6Gi"

    def test_first_session_id_wins(self, tmp_path: Path) -> None:
        # The first session.id seen is locked (this dispatch's own session,
        # created at run start); a later one from concurrent-dispatch noise
        # sharing the log file does not replace it.
        log = tmp_path / "opencode.log"
        log.write_text("baseline\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        with log.open("a", encoding="utf-8") as fh:
            fh.write("... session.id=ses_FIRST ...\n")
        mon.poll(time.monotonic())
        with log.open("a", encoding="utf-8") as fh:
            fh.write("... session.id=ses_SECOND ...\n")
        mon.poll(time.monotonic())
        assert mon.session_id == "ses_FIRST"

    def test_no_session_id_is_none(self, tmp_path: Path) -> None:
        log = tmp_path / "opencode.log"
        log.write_text("baseline without a session marker\n", encoding="utf-8")
        mon = _PermissionAskMonitor(str(log))
        mon.poll(time.monotonic())
        assert mon.session_id is None


# ── IdleWatchdog: permission-ask deadlock kill condition ───────────────────


class TestIdleWatchdogPermissionDeadlock:
    """An unanswered permission ask aged past the grace window kills the run."""

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_unanswered_ask_kills_run(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A server-log `message=asking` that persists past grace → kill."""
        server_log = tmp_path / "opencode.log"
        server_log.write_text("baseline\n", encoding="utf-8")

        proc = _mock_proc(returncode=None)
        proc.returncode = -15
        # First poll: running (ask detected, but not yet aged). Second poll:
        # still running; ask now aged past grace → kill. (poll() called once at
        # top of loop before the kill checks.)
        proc.poll.side_effect = [None, -15]
        mock_getpgid.return_value = 12345

        # Pretend the run started long ago so the ask (recorded on first poll)
        # is already aged past the grace window by the kill check.
        far_past = time.monotonic() - 1000
        state = WatchdogState(far_past)
        # Keep the client "active" so only the permission-deadlock path fires
        # (idle timeout must NOT be the reason).
        state._last_line_time = time.monotonic()

        cfg = WatchdogConfig(
            idle_timeout_secs=10000,  # disable idle path
            hard_ceiling_secs=None,  # disable ceiling
            poll_interval_secs=0,
            permission_ask_grace_secs=1,
            server_log_path=str(server_log),
        )
        wd = IdleWatchdog(proc, state, cfg)
        # Force the monitor to already have a pending, aged ask before run().
        wd._ask_monitor._last_ask_time = far_past
        wd._ask_monitor._last_ask_detail = (
            "message=asking permission=external_directory patterns=[/tmp/kilo/x/*]"
        )

        result = wd.run()

        assert result.killed is True
        assert result.reason == REASON_PERMISSION_DEADLOCK
        mock_killpg.assert_called()

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_ask_within_grace_does_not_kill(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """A fresh ask (younger than grace) must not kill — process exits."""
        server_log = tmp_path / "opencode.log"
        server_log.write_text("baseline\n", encoding="utf-8")

        proc = _mock_proc(returncode=None)
        proc.returncode = 0
        proc.poll.side_effect = [None, 0]  # exits on second poll
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic())
        state._last_line_time = time.monotonic()  # client active

        cfg = WatchdogConfig(
            idle_timeout_secs=10000,
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            permission_ask_grace_secs=1000,  # ask far within grace
            server_log_path=str(server_log),
        )
        wd = IdleWatchdog(proc, state, cfg)
        # Pending ask recorded "just now" → younger than the 1000s grace.
        wd._ask_monitor._last_ask_time = time.monotonic()
        wd._ask_monitor._last_ask_detail = "message=asking permission=bash"

        result = wd.run()

        assert result.killed is False
        assert result.reason == REASON_PROCESS_EXIT
        mock_killpg.assert_not_called()


# ── Server-side session abort on termination ────────────────────────────────


class TestServerSessionAbort:
    """On any watchdog trip the server-side opencode session is aborted via
    ``POST {server}/session/{id}/abort`` so a killed client does not leave an
    orphaned server session. Client and server run in separate containers, so a
    client process-group kill alone cannot stop the server-side agent loop."""

    @staticmethod
    def _make_wd(
        *,
        session_id: str | None = "ses_abc",
        server_url: str = "http://orchestratorservice:4099",
    ) -> IdleWatchdog:
        proc = _mock_proc(returncode=-15)
        state = WatchdogState(time.monotonic())
        cfg = WatchdogConfig(
            server_url=server_url,
            server_password="secret",
            server_username="opencode",
            server_log_path="",  # disable server-log signal
            sigterm_grace_secs=0,
        )
        wd = IdleWatchdog(proc, state, cfg)
        wd._ask_monitor._session_id = session_id
        return wd

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    @patch("webhook_receiver.watchdog.urllib.request.urlopen")
    def test_abort_posts_to_session_endpoint(
        self,
        mock_urlopen: MagicMock,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """Termination POSTs to /session/{id}/abort with basic auth."""
        mock_getpgid.return_value = 1
        ctx = mock_urlopen.return_value
        ctx.__enter__.return_value = ctx
        ctx.status = 200

        wd = self._make_wd(session_id="ses_abc")
        wd._terminate(REASON_IDLE_TIMEOUT)

        mock_urlopen.assert_called_once()
        req: urllib.request.Request = mock_urlopen.call_args.args[0]
        assert req.full_url == "http://orchestratorservice:4099/session/ses_abc/abort"
        assert req.get_method() == "POST"
        auth = req.get_header("Authorization")
        assert auth is not None
        assert auth.startswith("Basic ")

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    @patch("webhook_receiver.watchdog.urllib.request.urlopen")
    def test_abort_skipped_without_session_id(
        self,
        mock_urlopen: MagicMock,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """No captured session id → no abort attempt, but client kill proceeds."""
        mock_getpgid.return_value = 1
        wd = self._make_wd(session_id=None)
        wd._terminate(REASON_IDLE_TIMEOUT)
        mock_urlopen.assert_not_called()
        mock_killpg.assert_called()

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    @patch("webhook_receiver.watchdog.urllib.request.urlopen")
    def test_abort_skipped_without_server_url(
        self,
        mock_urlopen: MagicMock,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """Empty server_url → abort disabled, client kill still proceeds."""
        mock_getpgid.return_value = 1
        wd = self._make_wd(session_id="ses_abc", server_url="")
        wd._terminate(REASON_IDLE_TIMEOUT)
        mock_urlopen.assert_not_called()
        mock_killpg.assert_called()

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    @patch("webhook_receiver.watchdog.urllib.request.urlopen")
    def test_abort_failure_is_swallowed(
        self,
        mock_urlopen: MagicMock,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
    ) -> None:
        """A network failure during abort must not prevent the client kill."""
        mock_getpgid.return_value = 1
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        wd = self._make_wd(session_id="ses_abc")
        # Must not raise — best-effort cleanup.
        wd._terminate(REASON_IDLE_TIMEOUT)
        mock_urlopen.assert_called_once()
        mock_killpg.assert_called()
