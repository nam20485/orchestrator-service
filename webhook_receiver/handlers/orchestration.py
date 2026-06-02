from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from typing_extensions import override

from webhook_receiver.config import Settings
from webhook_receiver.handlers.base import EventHandler, WebhookContext
from webhook_receiver.prompts import build_orchestrator_prompt
from webhook_receiver.runner import dispatch_to_opencode

logger = logging.getLogger(__name__)


class OrchestrationHandler(EventHandler):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @override
    def matches(self, ctx: WebhookContext) -> bool:
        return True

    @override
    def handle(self, ctx: WebhookContext) -> JSONResponse:
        prompt = build_orchestrator_prompt(
            delivery_id=ctx.delivery_id,
            event=ctx.event,
            payload=ctx.payload,
            max_payload_chars=ctx.settings.max_payload_chars,
        )

        ctx.background_tasks.add_task(dispatch_to_opencode, ctx.settings, prompt)

        logger.info(
            "Accepted delivery_id=%s event=%s action=%s",
            ctx.delivery_id,
            ctx.event,
            ctx.payload.get("action"),
        )
        return JSONResponse(
            {
                "status": "accepted",
                "delivery_id": ctx.delivery_id,
                "event": ctx.event,
            },
            status_code=202,
        )
