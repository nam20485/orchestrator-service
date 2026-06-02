from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

from webhook_receiver.config import Settings


@dataclass(frozen=True)
class WebhookContext:
    delivery_id: str
    event: str
    payload: dict[str, Any]
    settings: Settings
    background_tasks: BackgroundTasks


class EventHandler(ABC):
    @abstractmethod
    def matches(self, ctx: WebhookContext) -> bool: ...

    @abstractmethod
    def handle(self, ctx: WebhookContext) -> JSONResponse: ...
