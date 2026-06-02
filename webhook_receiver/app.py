from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from webhook_receiver.config import Settings
from webhook_receiver.github import verify_signature
from webhook_receiver.handlers import WebhookContext, build_handler_registry
from webhook_receiver.simulator import create_simulator_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    registry = build_handler_registry(cfg)
    app = FastAPI(
        title="Orchestrator GitHub Webhook Receiver",
        version="0.1.0",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request, background_tasks: BackgroundTasks
    ) -> JSONResponse:
        body = await request.body()
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        event = request.headers.get("X-GitHub-Event", "").lower()
        signature = request.headers.get("X-Hub-Signature-256")

        if len(body) > cfg.max_body_bytes:
            logger.warning(
                "Rejected webhook delivery_id=%s event=%s (body too large: %s bytes)",
                delivery_id,
                event,
                len(body),
            )
            raise HTTPException(status_code=413, detail="Request body too large")

        if not verify_signature(body, signature, cfg.github_webhook_secret):
            logger.warning(
                "Rejected webhook delivery_id=%s event=%s (bad signature)",
                delivery_id,
                event,
            )
            raise HTTPException(status_code=401, detail="Invalid signature")

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

        ctx = WebhookContext(
            delivery_id=delivery_id,
            event=event,
            payload=payload,
            settings=cfg,
            background_tasks=background_tasks,
        )
        return registry.dispatch(ctx)

    app.include_router(create_simulator_router(enabled=cfg.enable_simulator))

    return app
