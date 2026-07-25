from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from webhook_receiver.auth import make_dashboard_token_dep, persist_token_cookie
from webhook_receiver.github import compute_signature
from webhook_receiver.simulator_templates import ALL_EVENTS, get_template, list_templates

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_simulator_router(
    *, enabled: bool, port: int, dashboard_token: str | None = None
) -> APIRouter:
    if not enabled:
        unauthed = APIRouter(prefix="/simulator", tags=["simulator"])

        @unauthed.get("")
        @unauthed.get("/{path:path}")
        async def simulator_disabled(path: str = "") -> None:
            raise HTTPException(status_code=404, detail="Simulator disabled")

        return unauthed

    router = APIRouter(
        prefix="/simulator",
        tags=["simulator"],
        # The simulator can mint signatures over OS_WEBHOOK_SECRET and forward
        # them to /webhooks/github, so it is at least as privileged as the
        # dashboard. Because Caddy proxies the whole receiver surface (not just
        # /webhooks/github), an enabled simulator reached through a tunnel
        # (Funnel/ngrok) must require a token. Shares the dashboard's token
        # extraction/cookie logic via webhook_receiver.auth (no drift).
        dependencies=[
            Depends(
                make_dashboard_token_dep(
                    dashboard_token,
                    disabled_status=401,
                    disabled_detail="Simulator requires DASHBOARD_TOKEN to be set",
                )
            )
        ],
    )

    @router.get("")
    async def simulator_page(request: Request) -> HTMLResponse:
        html_path = _STATIC_DIR / "simulator.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Simulator UI not found")
        html = html_path.read_text(encoding="utf-8")
        resp = HTMLResponse(
            html,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
        # Persist a valid ?token= so subsequent same-origin fetch() calls to
        # /simulator/api/* authenticate automatically (mirrors the dashboard).
        persist_token_cookie(resp, request, dashboard_token)
        return resp

    @router.get("/api/templates")
    async def template_list(
        safe_only: bool = Query(False, description="Return ping-only templates"),
    ) -> dict[str, list[str]]:
        return {"events": list_templates(safe_only=safe_only)}

    @router.get("/api/templates/{event}")
    async def template_for_event(
        event: str,
        repo: str = Query("org/repo"),
        action: str | None = Query(None),
        number: int | None = Query(None),
    ) -> dict[str, object]:
        event_key = event.lower()
        if event_key not in ALL_EVENTS:
            raise HTTPException(status_code=404, detail=f"Unknown event: {event}")
        try:
            payload = get_template(event_key, repo=repo, action=action, number=number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"event": event_key, "payload": payload}

    @router.post("/api/send")
    async def simulator_send(request: Request) -> JSONResponse:
        """Sign a payload server-side and forward it to the webhook receiver.

        The ``OS_WEBHOOK_SECRET`` never leaves the server process, so the
        simulator UI cannot leak it to unauthenticated visitors.
        """
        try:
            data = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

        event = str(data.get("event", "")).lower()
        if not event:
            raise HTTPException(status_code=400, detail="event is required")
        delivery_id = str(data.get("deliveryId") or "")
        payload = data.get("payload")
        if payload is None:
            raise HTTPException(status_code=400, detail="payload is required")

        secret = os.environ.get("OS_WEBHOOK_SECRET", "").strip()
        if not secret:
            raise HTTPException(status_code=500, detail="OS_WEBHOOK_SECRET not configured")

        body = json.dumps(payload).encode("utf-8")
        signature = compute_signature(body, secret)

        # Forward over loopback to this same process. ``request.base_url``
        # reflects the external Host the browser used to reach the page; when
        # the simulator is accessed through a tunnel/reverse proxy (Funnel,
        # ngrok, etc.) the container cannot route back to its own public
        # hostname and the round-trip fails ("All connection attempts failed").
        forward_url = f"http://127.0.0.1:{port}/webhooks/github"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    forward_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-GitHub-Event": event,
                        "X-GitHub-Delivery": delivery_id,
                        "X-Hub-Signature-256": signature,
                    },
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Webhook forward failed: {exc}") from exc

        return JSONResponse(
            {"status": resp.status_code, "body": resp.text},
            status_code=200,
        )

    return router
