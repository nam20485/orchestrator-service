from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from webhook_receiver.config import Settings
from webhook_receiver.github import verify_signature
from webhook_receiver.prompts import build_orchestrator_prompt
from webhook_receiver.runner import dispatch_to_opencode
from webhook_receiver.simulator import create_simulator_router

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
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

        if event == "ping":
            return JSONResponse(
                {"status": "pong", "delivery_id": delivery_id},
                status_code=200,
            )

        if cfg.allowed_events is not None and event not in cfg.allowed_events:
            logger.info(
                "Ignored delivery_id=%s event=%s (not in allow list)",
                delivery_id,
                event,
            )
            return JSONResponse(
                {
                    "status": "ignored",
                    "delivery_id": delivery_id,
                    "event": event,
                    "reason": "event not in WEBHOOK_ALLOWED_EVENTS",
                },
                status_code=202,
            )

        try:
            payload: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

        prompt = build_orchestrator_prompt(
            delivery_id=delivery_id,
            event=event,
            payload=payload,
            max_payload_chars=cfg.max_payload_chars,
        )

        background_tasks.add_task(dispatch_to_opencode, cfg, prompt)

        logger.info(
            "Accepted delivery_id=%s event=%s action=%s",
            delivery_id,
            event,
            payload.get("action"),
        )
        return JSONResponse(
            {
                "status": "accepted",
                "delivery_id": delivery_id,
                "event": event,
            },
            status_code=202,
        )

    app.include_router(create_simulator_router(enabled=cfg.enable_simulator))

    return app
