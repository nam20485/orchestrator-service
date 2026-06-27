from __future__ import annotations

import asyncio
import glob
import hmac
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse

from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.event_store import EventStore

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 5.0
# bvr pages bundles are more expensive to regenerate than a `br list`, so they
# are cached for longer than the per-request beads view.
_PAGES_TTL = 60.0
_MAX_SSE_SUBSCRIBERS = 10

# bvr pages bundles live under the temp dir alongside per-bead agent logs.
_PAGES_SUBDIR = "bvr-pages"

# Stand-in page shown when no `.beads` graph exists yet (normal idle state).
_NOT_INITIALIZED_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<style>body{font-family:system-ui,sans-serif;background:#0f1419;color:#8b949e;"
    "display:flex;align-items:center;justify-content:center;height:90vh;margin:0}"
    "code{background:#1e293b;padding:0.1rem 0.35rem;border-radius:3px;color:#e6edf3}</style></head>"
    "<body><div style='text-align:center'>"
    "<h2 style='color:#e6edf3;margin-bottom:0.4rem'>Beads not initialized</h2>"
    "<p>Trigger <code>/plan-to-beads</code> to create the dependency graph.</p>"
    "</div></body></html>"
)


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


def _ui_status(
    bead_id: Any,
    db_status: str,
    active: set[Any],
    halted: set[Any],
    ready_ids: set[Any],
) -> str:
    """Map a bead to its dashboard status, shared by the list + graph views.

    Priority order mirrors the existing ``/beads`` endpoint: active, halted,
    closed, ready, then blocked. ``db_status`` is the raw ``br`` status
    (lower-cased by the caller).
    """
    if bead_id in active:
        return "active"
    if bead_id in halted:
        return "halted"
    if db_status == "closed":
        return "closed"
    if bead_id in ready_ids:
        return "ready"
    if db_status in ("open", ""):
        return "blocked"
    return db_status


def _fetch_beads_graph(ws: str) -> dict[str, Any]:
    """Fetch the dependency graph + node metadata via ``br``.

    Returns ``{nodes, edges, initialized, meta, ready_ids}`` where ``nodes`` and
    ``edges`` come from ``br graph --all --json`` (defensive on shape) and
    ``meta``/``ready_ids`` are pulled from ``br list``/``br ready`` for richer
    node attributes and loop-state enrichment.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    initialized = False

    graph_out = _run_beads_cmd(["br", "graph", "--all", "--json"], ws)
    if graph_out:
        try:
            gdata = json.loads(graph_out)
        except json.JSONDecodeError:
            gdata = {}
        if isinstance(gdata, dict):
            for comp in gdata.get("components", []) or []:
                if not isinstance(comp, dict):
                    continue
                for n in comp.get("nodes", []) or []:
                    if isinstance(n, dict):
                        nodes.append(n)
                for e in comp.get("edges", []) or []:
                    # ``br`` emits edges as ``[dependent_id, dependency_id]``.
                    if isinstance(e, (list, tuple)) and len(e) == 2:
                        edges.append({"source": e[0], "target": e[1]})
            initialized = bool(gdata.get("total_nodes")) or bool(nodes)

    all_beads = _parse_beads(_run_beads_cmd(["br", "list", "--json"], ws))
    ready = _parse_beads(_run_beads_cmd(["br", "ready", "--json"], ws))
    return {
        "nodes": nodes,
        "edges": edges,
        "initialized": initialized,
        "meta": {b.get("id"): b for b in all_beads if isinstance(b, dict)},
        "ready_ids": {b.get("id") for b in ready},
    }


# ── bvr static-pages bundle ──────────────────────────────────────────────────


def _bvr_bundle_dir() -> Path:
    return Path(tempfile.gettempdir()) / "orchestrator-webhook" / _PAGES_SUBDIR


def _run_bvr_export(args: list[str], cwd: str) -> None:
    """Run ``bvr`` to (re)generate the static pages bundle. Raises on failure."""
    subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "RUST_LOG": "error"},
    )


def _generate_pages_bundle(ws: str, force: bool = False) -> tuple[bool, str | None]:
    """Ensure the bvr static-pages bundle exists for *ws*.

    Returns ``(ok, error)``. ``ok`` is True when a usable bundle is available
    (cached or freshly generated). When ``.beads`` is absent the result is
    ``(False, "not_initialized")`` — a normal idle state, never raised.
    """
    now = time.time()
    if not force and "pages_bundle" in _CACHE:
        ts, _ = _CACHE["pages_bundle"]
        if now - ts < _PAGES_TTL:
            return True, None

    # "Beads not initialized" is a normal startup state: do not attempt the
    # (failing) export or log it as an error.
    if not Path(ws, ".beads").exists():
        return False, "not_initialized"

    bundle = _bvr_bundle_dir()
    bundle.mkdir(parents=True, exist_ok=True)
    try:
        _run_bvr_export(
            ["bvr", "--export-pages", str(bundle), "--pages-include-history", "false"],
            ws,
        )
    except FileNotFoundError:
        logger.warning("bvr binary not found; cannot generate pages bundle")
        return False, "bvr_not_installed"
    except subprocess.CalledProcessError as exc:
        logger.error(
            "bvr pages export failed: %s",
            (exc.stderr or exc.stdout or "")[:300],
        )
        return False, "export_failed"

    _CACHE["pages_bundle"] = (now, bundle)
    return True, None


def _make_dashboard_auth(token: str | None):
    """Build a FastAPI dependency that gates every dashboard route.

    The dashboard exposes bead metadata and agent stdout/stderr, which may
    contain secrets or repo data. Because Caddy proxies the whole receiver
    surface (not just ``/webhooks/github``), these endpoints must be gated:

    * If no ``DASHBOARD_TOKEN`` is configured the dashboard is **disabled by
      default** and every route returns ``404``.
    * When configured, a request must present the token via an
      ``Authorization: Bearer <token>`` header, a ``?token=`` query parameter,
      or a ``dashboard_token`` cookie. Constant-time comparison is used.
    """

    async def _require_token(request: Request) -> None:
        if not token:
            raise HTTPException(
                status_code=404, detail="Dashboard is disabled (DASHBOARD_TOKEN not set)"
            )
        provided: str | None = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            provided = auth_header.split(None, 1)[1].strip()
        if not provided:
            provided = request.query_params.get("token")
        if not provided:
            provided = request.cookies.get("dashboard_token")
        if not provided or not hmac.compare_digest(str(provided), token):
            raise HTTPException(
                status_code=401, detail="Invalid or missing dashboard token"
            )

    return _require_token


def _fetch_beads_view(ws: str) -> dict[str, Any]:
    """Fetch all beads and ready bead IDs via br CLI. Shared by all endpoints."""
    all_beads = _parse_beads(_run_beads_cmd(["br", "list", "--json"], ws))
    ready = _parse_beads(_run_beads_cmd(["br", "ready", "--json"], ws))
    return {"all": all_beads, "ready_ids": {b.get("id") for b in ready}}


def _enrich_beads(
    cached: dict[str, Any], beads_loop: BeadsLoop | None
) -> list[dict[str, Any]]:
    """Enrich raw beads with runtime status from the loop.

    Shared by the ``/beads`` list and ``/beads/{bead_id}`` detail endpoints so
    both surface identical ``ui_status``/``elapsed_s``/``retry_count`` values.
    """
    all_beads: list[dict[str, Any]] = cached["all"]
    ready_ids: set[Any] = cached["ready_ids"]

    active: set[str] = set()
    retry: dict[str, dict[str, object]] = {}
    start_times: dict[str, float] = {}
    halted_ids: set[str] = set()
    now = time.time()
    if beads_loop:
        active = set(beads_loop.active_beads)
        retry = beads_loop.retry_state
        start_times = beads_loop.bead_start_times
        halted_ids = set(beads_loop.halted_beads)

    enriched: list[dict[str, Any]] = []
    for b in all_beads:
        bid = b.get("id", "")
        db_status = str(b.get("status", "")).lower()
        retry_count = 0
        rstate = retry.get(bid)
        if rstate and isinstance(rstate.get("count"), (int, float)):
            retry_count = int(rstate["count"])

        ui_status = _ui_status(bid, db_status, active, halted_ids, ready_ids)
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


def create_dashboard_router(
    event_store: EventStore,
    beads_loop: BeadsLoop | None = None,
    dashboard_token: str | None = None,
) -> APIRouter:
    auth = _make_dashboard_auth(dashboard_token)
    router = APIRouter(
        prefix="/api/dashboard",
        tags=["dashboard"],
        dependencies=[Depends(auth)],
    )

    # ── overview ───────────────────────────────────────────────────────────

    @router.get("/overview")
    async def overview() -> dict[str, Any]:
        ws = _workspace()
        cached = await asyncio.to_thread(_cached, "beads_view", _fetch_beads_view, ws)
        all_beads: list[dict[str, Any]] = cached["all"]
        ready_ids: set[Any] = cached["ready_ids"]

        total = len(all_beads)
        closed = sum(1 for b in all_beads if str(b.get("status", "")).lower() == "closed")
        open_beads = [b for b in all_beads if str(b.get("status", "")).lower() != "closed"]

        active: set[str] = set()
        halted: set[str] = set()
        max_retries = 3
        running = False
        poll_interval = 10
        if beads_loop:
            active = set(beads_loop.active_beads)
            halted = set(beads_loop.halted_beads)
            running = beads_loop._running
            max_retries = beads_loop._settings.beads_max_retries
            poll_interval = beads_loop._settings.beads_poll_interval

        excluded = active | halted
        ready_count = len([
            b for b in open_beads
            if b.get("id") in ready_ids and b.get("id") not in excluded
        ])
        blocked_count = len(open_beads) - ready_count - len([
            b for b in open_beads if b.get("id") in excluded
        ])

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
        cached = await asyncio.to_thread(_cached, "beads_view", _fetch_beads_view, ws)
        return _enrich_beads(cached, beads_loop)

    @router.get("/beads/{bead_id}")
    async def bead_detail(bead_id: str) -> dict[str, Any]:
        if not _valid_bead_id(bead_id):
            raise HTTPException(status_code=400, detail="Invalid bead ID")
        ws = _workspace()
        cached = await asyncio.to_thread(_cached, "beads_view", _fetch_beads_view, ws)
        enriched = _enrich_beads(cached, beads_loop)
        bead = next((b for b in enriched if b.get("id") == bead_id), None)
        if bead is None:
            raise HTTPException(status_code=404, detail="Bead not found")
        return bead

    # ── dependency graph ────────────────────────────────────────────────────

    @router.get("/graph")
    async def graph() -> dict[str, Any]:
        ws = _workspace()
        g = await asyncio.to_thread(
            _cached, "beads_graph", _fetch_beads_graph, ws
        )

        active: set[Any] = set()
        halted: set[Any] = set()
        if beads_loop:
            active = set(beads_loop.active_beads)
            halted = set(beads_loop.halted_beads)

        meta = g["meta"]
        ready_ids = g["ready_ids"]
        nodes: list[dict[str, Any]] = []
        for n in g["nodes"]:
            bid = n.get("id", "")
            m = meta.get(bid, {})
            db_status = str(m.get("status", n.get("status", ""))).lower()
            nodes.append(
                {
                    "id": bid,
                    "title": m.get("title", n.get("title", bid)),
                    "type": m.get("issue_type", n.get("type", "task")),
                    "priority": m.get("priority", n.get("priority", 999)),
                    "status": db_status or "open",
                    "depth": n.get("depth", 0),
                    "ui_status": _ui_status(bid, db_status, active, halted, ready_ids),
                }
            )

        return {
            "nodes": nodes,
            "edges": g["edges"],
            "initialized": g["initialized"],
        }

    # ── bvr pages bundle ────────────────────────────────────────────────────

    @router.post("/pages/refresh")
    async def pages_refresh() -> dict[str, Any]:
        ws = _workspace()
        ok, err = await asyncio.to_thread(_generate_pages_bundle, ws, True)
        if not ok:
            # ``initialized`` mirrors whether ``.beads`` exists, so the UI can
            # distinguish "not initialized" from a transient export failure.
            return {"ok": False, "error": err, "initialized": err != "not_initialized"}
        return {"ok": True, "generated_at": time.time()}

    # ── bead detail + logs ─────────────────────────────────────────────────

    @router.get("/beads/{bead_id}/logs")
    async def bead_logs(bead_id: str, tail: int = 200) -> dict[str, Any]:
        if not _valid_bead_id(bead_id):
            raise HTTPException(status_code=400, detail="Invalid bead ID")
        tail = max(1, min(tail, 2000))
        log_dir = Path(tempfile.gettempdir()) / "orchestrator-webhook"
        safe_id = glob.escape(bead_id)

        def _read_latest(suffix: str) -> str:
            files = sorted(
                glob.glob(str(log_dir / f"bead-{safe_id}-*{suffix}")),
                key=os.path.getmtime,
                reverse=True,
            )
            if not files:
                return ""
            content = Path(files[0]).read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
            return "\n".join(lines[-tail:])

        stdout_tail, stderr_tail = await asyncio.to_thread(
            lambda: (
                _read_latest(".stdout"),
                _read_latest(".stderr"),
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
        cached = await asyncio.to_thread(_cached, "beads_view", _fetch_beads_view, ws)
        bead_map = {b.get("id", ""): b for b in cached["all"]}

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
        if event_store.subscriber_count >= _MAX_SSE_SUBSCRIBERS:
            raise HTTPException(status_code=503, detail="Too many SSE subscribers")

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


def _serve_html(
    request: Request, filename: str, dashboard_token: str | None
) -> HTMLResponse:
    """Serve a static dashboard page and persist the token cookie if present.

    When the page is opened with ``?token=<token>``, persist it as a cookie so
    subsequent same-origin ``fetch()``/``EventSource`` requests authenticate
    automatically (and so a new tab opened via ``window.open`` is authed).
    """
    html_path = _STATIC_DIR / filename
    if not html_path.is_file():
        raise HTTPException(status_code=500, detail=f"{filename} not found")
    html = html_path.read_text(encoding="utf-8")
    resp = HTMLResponse(
        html,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )
    query_token = request.query_params.get("token")
    if query_token and hmac.compare_digest(query_token, str(dashboard_token or "")):
        resp.set_cookie(
            "dashboard_token",
            query_token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
    return resp


def _valid_bead_id(bead_id: str | None) -> bool:
    """Glob/path-safe bead ID check shared by the detail page + metadata."""
    return bool(bead_id) and bead_id.replace("-", "").replace("_", "").isalnum()


def create_dashboard_page_router(dashboard_token: str | None = None) -> APIRouter:
    auth = _make_dashboard_auth(dashboard_token)
    router = APIRouter(tags=["dashboard"], dependencies=[Depends(auth)])

    @router.get("/dashboard")
    async def dashboard_page(request: Request) -> HTMLResponse:
        return _serve_html(request, "dashboard.html", dashboard_token)

    @router.get("/dashboard/bead/{bead_id}")
    async def bead_detail_page(request: Request, bead_id: str) -> HTMLResponse:
        if not _valid_bead_id(bead_id):
            raise HTTPException(status_code=400, detail="Invalid bead ID")
        return _serve_html(request, "bead_detail.html", dashboard_token)

    return router


# ── bvr static-pages bundle serving (token-gated) ───────────────────────────


def create_dashboard_pages_router(dashboard_token: str | None = None) -> APIRouter:
    """Serve the bvr static-pages bundle behind the dashboard token.

    Routes (both gated by the same auth dependency as the rest of the dashboard):

    * ``GET /dashboard/pages``    → redirect to ``/dashboard/pages/``
    * ``GET /dashboard/pages/``   → the bundle ``index.html``
    * ``GET /dashboard/pages/{path}`` → any bundle sub-asset (CSS/JS/data/...)

    The index is served at a trailing-slash URL because the bvr bundle uses
    *relative* asset references (``styles.css``, ``vendor/...``); only a
    trailing-slash base URL resolves those correctly.
    """
    auth = _make_dashboard_auth(dashboard_token)
    router = APIRouter(tags=["dashboard"], dependencies=[Depends(auth)])

    @router.get("/dashboard/pages")
    async def pages_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/pages/", status_code=307)

    @router.get("/dashboard/pages/{file_path:path}")
    async def pages_serve(file_path: str) -> Response:
        ws = _workspace()
        ok, _ = await asyncio.to_thread(_generate_pages_bundle, ws, False)
        bundle = _bvr_bundle_dir()

        if file_path in ("", "index.html"):
            if not ok or not (bundle / "index.html").is_file():
                return HTMLResponse(_NOT_INITIALIZED_HTML, status_code=200)
            return FileResponse(str(bundle / "index.html"), media_type="text/html")

        # Path-traversal guard: resolve within the bundle root.
        bundle_root = bundle.resolve()
        target = (bundle_root / file_path).resolve()
        try:
            target.relative_to(bundle_root)
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(str(target))

    return router
