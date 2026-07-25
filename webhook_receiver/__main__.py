from __future__ import annotations

import logging
import sys
import threading

import uvicorn

from webhook_receiver.app import create_app
from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings
from webhook_receiver.event_store import EventStore
from webhook_receiver.observability import init_sentry

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

    if init_sentry(settings):
        logger.info(
            "Sentry error tracking initialized (environment=%s)", settings.sentry_environment
        )

    event_store = EventStore()
    loop: BeadsLoop | None = None
    if settings.beads_enabled:
        loop = BeadsLoop(settings, event_store=event_store)
        thread = threading.Thread(target=loop.run, daemon=True, name="beads-loop")
        thread.start()
        logger.info(
            "BeadsLoop background thread started (poll_interval=%ds)",
            settings.beads_poll_interval,
        )
    else:
        logger.info("BeadsLoop disabled (BEADS_ENABLED=false)")

    uvicorn.run(
        create_app(settings, event_store=event_store, beads_loop=loop),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
