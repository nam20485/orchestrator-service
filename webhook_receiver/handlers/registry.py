from __future__ import annotations

from collections.abc import Sequence

from fastapi.responses import JSONResponse

from webhook_receiver.config import Settings
from webhook_receiver.handlers.base import EventHandler, WebhookContext
from webhook_receiver.handlers.ignored import IgnoredEventHandler
from webhook_receiver.handlers.orchestration import OrchestrationHandler
from webhook_receiver.handlers.ping import PingHandler


class HandlerRegistry:
    def __init__(self, handlers: Sequence[EventHandler]) -> None:
        self._handlers = tuple(handlers)

    def dispatch(self, ctx: WebhookContext) -> JSONResponse:
        for handler in self._handlers:
            if handler.matches(ctx):
                return handler.handle(ctx)
        raise RuntimeError(f"No handler for event {ctx.event!r}")


def build_handler_registry(settings: Settings) -> HandlerRegistry:
    return HandlerRegistry(
        [
            PingHandler(),
            IgnoredEventHandler(settings),
            OrchestrationHandler(settings),
        ]
    )
