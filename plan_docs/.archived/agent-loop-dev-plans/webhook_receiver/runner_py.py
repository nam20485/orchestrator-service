from __future__ import annotations

import logging
import threading
from pathlib import Path

from webhook_receiver.config import Settings
from webhook_receiver.ralph_loop import run_loop

logger = logging.getLogger(__name__)

# Track active loops so we don't run two loops in the same workspace concurrently
_active_loops: set[str] = set()
_loop_lock = threading.Lock()


def dispatch_to_opencode(settings: Settings, prompt: str) -> None:
    """
    Replaces the old procedural dispatcher.
    Wakes up the Graph State Machine (Ralph Loop) for the configured workspace.
    """
    workspace = settings.workspace

    with _loop_lock:
        if workspace in _active_loops:
            logger.info(f"Agent loop already running for workspace={workspace}. Ignoring trigger.")
            return

        # Mark this workspace as currently active
        _active_loops.add(workspace)

    logger.info(f"Dispatching Ralph Loop for workspace={workspace}")

    try:
        # We trigger the synchronous loop. Since this function is called via
        # FastAPI BackgroundTasks in app.py, this will naturally run in the background.
        run_loop(repo_path=workspace)
    except Exception as e:
        logger.error(f"Fatal error in Ralph Loop for {workspace}: {e}", exc_info=True)
    finally:
        # Ensure we release the lock on this workspace when the loop goes back to sleep
        with _loop_lock:
            if workspace in _active_loops:
                _active_loops.remove(workspace)
            logger.info(f"Ralph Loop finished and lock released for workspace={workspace}")
