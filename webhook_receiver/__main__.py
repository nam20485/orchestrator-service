from __future__ import annotations

import logging
import sys
import threading

import uvicorn

from webhook_receiver.app import create_app
from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if settings.beads_enabled:
        loop = BeadsLoop(settings)
        thread = threading.Thread(target=loop.run, daemon=True, name="beads-loop")
        thread.start()
        logger.info(
            "BeadsLoop background thread started (poll_interval=%ds)",
            settings.beads_poll_interval,
        )
    else:
        logger.info("BeadsLoop disabled (BEADS_ENABLED=false)")

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
