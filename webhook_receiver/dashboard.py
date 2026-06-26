from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.event_store import EventStore

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 5.0


def _run_beads_cmd(args: list[str], workspace: str) -> str:
    """Run a ``br``/``bvr`` command and return stdout (empty on error)."""
    try:
        result = subprocess.run(
            args,
            cwd=workspace,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "RUST_LOG": "error"},
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def _parse_beads(stdout: str) -> list[dict[str, Any]]:
    """Parse br/bvr JSON output into a list of bead dicts (defensive)."""
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        items = data.get("issues", data.get("beads", []))
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [b for b in items if isinstance(b, dict)]


def _cached(key: str, factory: Any, *args: Any) -> Any:
    """Simple TTL cache for expensive CLI calls."""
    now = time.time()
    if key in _CACHE:
        ts, val = _CACHE[key]
        if now - ts < _CACHE_TTL:
            return val
    val = factory(*args)
    _CACHE[key] = (now, val)
    return val


def _workspace() -> str:
    return os.environ.get("BEADS_WORKSPACE_ROOT", "/workspace")


def create_dashboard_router(
    event_store: EventStore,
    beads_loop: BeadsLoop | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

    # ── overview ───────────────────────────────────────────────────────────

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        ws = _workspace()

        def _fetch() -> dict[str, Any]:
            all_beads = _parse_beads(_run_beads_cmd(["br", "list", "--json"], ws))
            ready = _parse_beads(_run_beads_cmd(["br", "ready", "--json"], ws))
            return {"all": all_beads, "ready_ids": {b.get("id") for b in ready}}

        cached = await asyncio.to_thread(_cached, "overview", _fetch)
        all_beads: list[dict[str, Any]] = cached["all"]
        ready_ids: set[Any] = cached["ready_ids"]

        total = len(all_beads)
        closed = sum(1 for b in all_beads if str(b.get("status", "")).lower() == "closed")
        open_beads = [b for b in all_beads if str(b.get("status", "")).lower() != "closed"]
        ready_count = len([b for b in open_beads if b.get("id") in ready_ids])
        blocked_count = len(open_beads) - ready_count

        active: set[str] = set()
        retry: dict[str, dict[str, object]] = {}
        max_retries = 3
        running = False
        poll_interval = 10
        if beads_loop:
            active = beads_loop.active_beads
            retry = beads_loop.retry_state
            running = beads_loop._running
            max_retries = beads_loop._settings.beads_max_retries
            poll_interval = beads_loop._settings.beads_poll_interval

        halted = {
            bid
            for bid, state in retry.items()
            if isinstance(state.get("count"), (int, float))
            and state["count"] >= max_retries
        }

        return {
            "loop_status": {
                "running": running,
                "poll_interval": poll_interval,
                "max_retries": max_retries,
            },
            "counts": {
                "total": total,
                "open": len(open_beads),
                "ready": ready_count,
                "blocked": blocked_count,
                "active": len(active),
                "closed": closed,
                "halted": len(halted),
            },
            "initialized": total > 0,
        }

    # ── beads list ─────────────────────────────────────────────────────────

    @router.get("/beads")
    async def beads() -> list[dict[str, Any]]:
        ws = _workspace()

        def _fetch() -> dict[str, Any]:
            all_beads = _parse_beads(_run_beads_cmd(["br", "list", "--json"], ws))
            ready = _parse_beads(_run_beads_cmd(["br", "ready", "--json"], ws))
            return {"all": all_beads, "ready_ids": {b.get("id") for b in ready}}

        cached = await asyncio.to_thread(_cached, "beads", _fetch)
        all_beads: list[dict[str, Any]] = cached["all"]
        ready_ids: set[Any] = cached["ready_ids"]

        active: set[str] = set()
        retry: dict[str, dict[str, object]] = {}
        start_times: dict[str, float] = {}
        max_retries = 3
        now = time.time()
        if beads_loop:
            active = beads_loop.active_beads
            retry = beads_loop.retry_state
            start_times = beads_loop.bead_start_times
            max_retries = beads_loop._settings.beads_max_retries

        enriched: list[dict[str, Any]] = []
        for b in all_beads:
            bid = b.get("id", "")
            db_status = str(b.get("status", "")).lower()
            retry_count = 0
            rstate = retry.get(bid)
            if rstate and isinstance(rstate.get("count"), (int, float)):
                retry_count = int(rstate["count"])

            if bid in active:
                ui_status = "active"
            elif retry_count >= max_retries:
                ui_status = "halted"
            elif db_status == "closed":
                ui_status = "closed"
            elif bid in ready_ids:
                ui_status = "ready"
            elif db_status in ("open", ""):
                ui_status = "blocked"
            else:
                ui_status = db_status

            elapsed_s = None
            if bid in start_times:
                elapsed_s = round(now - start_times[bid], 1)

            enriched.append(
                {
                    "id": bid,
                    "title": b.get("title", bid),
                    "type": b.get("type", "task"),
                    "priority": b.get("priority", 999),
                    "status": db_status or "open",
                    "ui_status": ui_status,
                    "retry_count": retry_count,
                    "is_active": bid in active,
                    "elapsed_s": elapsed_s,
                    "description": b.get("description", ""),
                }
            )

        enriched.sort(key=lambda b: (b["ui_status"] != "active", b["priority"], b["id"]))
        return enriched

    # ── bead detail + logs ─────────────────────────────────────────────────

    @router.get("/beads/{bead_id}/logs")
    async def bead_logs(bead_id: str, tail: int = 200) -> dict[str, Any]:
        log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"

        def _read_latest(pattern: str) -> str:
            files = sorted(glob.glob(str(log_dir / pattern)), key=os.path.getmtime, reverse=True)
            if not files:
                return ""
            content = Path(files[0]).read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            return "\n".join(lines[-tail:])

        stdout_tail, stderr_tail = await asyncio.to_thread(
            lambda: (
                _read_latest(f"bead-{bead_id}-*.stdout"),
                _read_latest(f"bead-{bead_id}-*.stderr"),
            )
        )

        available = bool(stdout_tail or stderr_tail)
        return {
            "bead_id": bead_id,
            "stdout": stdout_tail,
            "stderr": stderr_tail,
            "available": available,
        }

    # ── active agents ──────────────────────────────────────────────────────

    @router.get("/active")
    async def active() -> list[dict[str, Any]]:
        if not beads_loop:
            return []

        active_ids = beads_loop.active_beads
        retry = beads_loop.retry_state
        start_times = beads_loop.bead_start_times
        now = time.time()

        ws = _workspace()

        def _fetch() -> dict[str, Any]:
            all_beads = _parse_beads(_run_beads_cmd(["br", "list", "--json"], ws))
            return {b.get("id", ""): b for b in all_beads}

        bead_map = await asyncio.to_thread(_cached, "beads_map", _fetch)

        result: list[dict[str, Any]] = []
        for bid in active_ids:
            bead = bead_map.get(bid, {})
            rstate = retry.get(bid, {})
            retry_count = int(rstate.get("count", 0)) if rstate else 0
            elapsed_s = round(now - start_times[bid], 1) if bid in start_times else None
            result.append(
                {
                    "bead_id": bid,
                    "title": bead.get("title", bid),
                    "retry_count": retry_count,
                    "elapsed_s": elapsed_s,
                }
            )
        return result

    # ── events ─────────────────────────────────────────────────────────────

    @router.get("/events")
    async def events(limit: int = 100) -> list[dict[str, Any]]:
        return event_store.recent(limit=limit)

    @router.get("/events/stream")
    async def events_stream() -> StreamingResponse:
        def generate():
            sub = event_store.subscribe()
            try:
                for event in sub:
                    if event is None:
                        yield ": keepalive\n\n"
                    else:
                        yield f"data: {json.dumps(event)}\n\n"
            finally:
                sub.close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


# ── HTML page route (separate so it has no /api prefix) ────────────────────


def create_dashboard_page_router() -> APIRouter:
    router = APIRouter(tags=["dashboard"])

    @router.get("/dashboard")
    async def dashboard_page() -> HTMLResponse:
        html_path = _STATIC_DIR / "dashboard.html"
        if not html_path.is_file():
            raise HTTPException(status_code=500, detail="Dashboard UI not found")
        html = html_path.read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    return router
