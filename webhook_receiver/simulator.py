from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from webhook_receiver.simulator_templates import ALL_EVENTS, get_template, list_templates

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_simulator_router(*, enabled: bool) -> APIRouter:
    router = APIRouter(prefix="/simulator", tags=["simulator"])

    if not enabled:

        @router.get("")
        @router.get("/{path:path}")
        async def simulator_disabled(path: str = "") -> None:
            raise HTTPException(status_code=404, detail="Simulator disabled")

        return router

    @router.get("")
    async def simulator_page() -> FileResponse:
        html_path = _STATIC_DIR / "simulator.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Simulator UI not found")
        return FileResponse(html_path, media_type="text/html")

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
            payload = get_template(
                event_key, repo=repo, action=action, number=number
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"event": event_key, "payload": payload}

    return router
