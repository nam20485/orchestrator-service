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
from pathlib import Path
from unittest.mock import MagicMock, patch

from webhook_receiver.watchdog import (
    REASON_CONSECUTIVE_ERRORS,
    REASON_HARD_CEILING,
    REASON_IDLE_TIMEOUT,
    REASON_PROCESS_EXIT,
    IdleWatchdog,
    SignalKind,
    WatchdogConfig,
    WatchdogState,
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
            classify_line(
                'timestamp=2026-07-11 level=ERROR run=70c74cd7 '
                'message="stream error"'
            )
            is SignalKind.ERROR
        )

    def test_ai_api_call_error_is_error(self) -> None:
        assert (
            classify_line(
                'error.error="AI_APICallError: Usage limit reached for 5 hour"'
            )
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


# ── Server-log mtime activity signal ─────────────────────────────────────


class TestServerLogActivitySignal:
    """Tests for the server-log mtime secondary activity signal.

    When the opencode client stdout goes silent during subagent delegation,
    the server log continues to be written. The watchdog checks the server
    log's mtime as a secondary signal to avoid false-positive idle kills.
    """

    @patch("webhook_receiver.watchdog.os.killpg")
    @patch("webhook_receiver.watchdog.os.getpgid")
    def test_server_log_active_withholds_kill(
        self,
        mock_getpgid: MagicMock,
        mock_killpg: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Server log recently written → kill withheld even if client is idle."""
        server_log = tmp_path / "opencode.log"
        server_log.write_text("recent server entry\n", encoding="utf-8")

        proc = _mock_proc(returncode=0)  # exits on its own eventually
        mock_getpgid.return_value = 12345

        state = WatchdogState(time.monotonic() - 1000)
        state._last_line_time = time.monotonic() - 1000  # client idle 1000s

        cfg = WatchdogConfig(
            idle_timeout_secs=1,  # would kill on client idle alone
            hard_ceiling_secs=None,
            poll_interval_secs=0,
            server_log_path=str(server_log),
        )
        wd = IdleWatchdog(proc, state, cfg)

        result = wd.run()

        # Server log mtime is recent → effective_idle is low → no kill.
        # Process exits on its own.
        assert result.killed is False
        assert result.reason == REASON_PROCESS_EXIT

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
