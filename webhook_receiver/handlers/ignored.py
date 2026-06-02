from __future__ import annotations

import logging

from fastapi.responses import JSONResponse

from webhook_receiver.config import Settings
from webhook_receiver.handlers.base import EventHandler, WebhookContext

logger = logging.getLogger(__name__)


class IgnoredEventHandler(EventHandler):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def matches(self, ctx: WebhookContext) -> bool:
        allowed = self._settings.allowed_events
        return allowed is not None and ctx.event not in allowed

    def handle(self, ctx: WebhookContext) -> JSONResponse:
        logger.info(
            "Ignored delivery_id=%s event=%s (not in allow list)",
            ctx.delivery_id,
            ctx.event,
        )
        return JSONResponse(
            {
                "status": "ignored",
                "delivery_id": ctx.delivery_id,
                "event": ctx.event,
                "reason": "event not in WEBHOOK_ALLOWED_EVENTS",
            },
            status_code=202,
        )
