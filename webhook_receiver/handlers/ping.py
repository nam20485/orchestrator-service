from __future__ import annotations

from fastapi.responses import JSONResponse

from webhook_receiver.handlers.base import EventHandler, WebhookContext


class PingHandler(EventHandler):
    def matches(self, ctx: WebhookContext) -> bool:
        return ctx.event == "ping"

    def handle(self, ctx: WebhookContext) -> JSONResponse:
        return JSONResponse(
            {"status": "pong", "delivery_id": ctx.delivery_id},
            status_code=200,
        )
