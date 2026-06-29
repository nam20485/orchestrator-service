from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from webhook_receiver.beads_loop import BeadsLoop
from webhook_receiver.config import Settings
from webhook_receiver.dashboard import (
    create_dashboard_page_router,
    create_dashboard_pages_router,
    create_dashboard_router,
)
from webhook_receiver.event_store import EventStore
from webhook_receiver.github import verify_signature
from webhook_receiver.prompts import build_orchestrator_prompt
from webhook_receiver.runner import dispatch_to_opencode
from webhook_receiver.simulator import create_simulator_router
from webhook_receiver.workspace import (
    ensure_project_from_clone,
    init_project_workspace,
    project_workspace_path,
    sync_project,
)

logger = logging.getLogger(__name__)

# Strict allowlist for project slugs derived from webhook payloads. Rejects
# path-traversal segments (``..``, ``.``, ``/``) and other shell-unsafe chars.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _derive_project_slug(payload: dict[str, Any]) -> str:
    """Derive a filesystem-safe project slug from a webhook payload.

    Uses ``repository.full_name`` (e.g. ``owner/repo``) sanitized to
    ``owner-repo``.  Falls back to ``repository.name`` then a constant.
    The result is validated against a strict allowlist to prevent path
    traversal.
    """
    repo = payload.get("repository", {})
    full_name = repo.get("full_name", "")
    if full_name:
        slug = full_name.replace("/", "-")
    else:
        slug = repo.get("name", "default-project")

    if not _SLUG_RE.match(slug):
        slug = "default-project"
    return slug


def _validate_clone_url(url: str) -> bool:
    """Return True if *url* is a safe HTTPS git clone URL.

    Rejects non-https schemes (``file://``, ``ssh://``, ``http://``) to
    prevent SSRF and local-file-read attacks via crafted webhook payloads.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _ensure_project_workspace(
    cfg: Settings, payload: dict[str, Any]
) -> tuple[str, Settings]:
    """Ensure a project workspace exists and return (project_root, project_settings).

    For webhooks from an existing repo, clones the repo on first arrival and
    syncs on subsequent arrivals (best-effort pull).  Returns a modified
    Settings with the workspace pointed at the project directory.
    """
    slug = _derive_project_slug(payload)
    base = cfg.beads_workspace_root
    repo = payload.get("repository", {})
    clone_url = repo.get("clone_url", "")

    project_root = project_workspace_path(base, slug)
    if _validate_clone_url(clone_url):
        ensure_project_from_clone(base, slug, clone_url)
        sync_project(project_root)

    project_settings = replace(cfg, workspace=project_root)
    return slug, project_settings


def _safe_dispatch(
    settings: Settings,
    prompt: str,
    store: EventStore,
    payload: dict[str, Any],
) -> None:
    """Background task: ensure workspace exists, then dispatch to opencode.

    Wraps the clone/sync in error handling so a failed clone does not crash
    the background worker.  Logs the error but still attempts the dispatch
    against whatever workspace state exists (a failed clone may leave a
    partial dir, but the agent can still work if the repo was previously
    cloned).
    """
    try:
        slug = _derive_project_slug(payload)
        base = settings.beads_workspace_root
        clone_url = payload.get("repository", {}).get("clone_url", "")
        resolved = project_workspace_path(base, slug)
        if os.path.realpath(resolved) == os.path.realpath(base):
            logger.error(
                "Refusing to dispatch to workspace root base=%s slug=%r",
                base,
                slug,
            )
            return
        if _validate_clone_url(clone_url):
            ensure_project_from_clone(base, slug, clone_url)
            sync_project(resolved)
        else:
            # No valid clone URL: bootstrap a fresh main-branch git repo so
            # later ``git worktree add`` (BeadsLoop) does not fail on a
            # missing ``.git``.
            init_project_workspace(base, slug)
    except Exception:
        logger.exception(
            "Failed to ensure project workspace for webhook dispatch; "
            "attempting dispatch with existing workspace state"
        )
    dispatch_to_opencode(settings, prompt, store)


def create_app(
    settings: Settings | None = None,
    event_store: EventStore | None = None,
    beads_loop: BeadsLoop | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = event_store or EventStore()
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

        logger.info(
            "Webhook received delivery_id=%s event=%s action=%s repo=%s sender=%s",
            delivery_id,
            event,
            payload.get("action"),
            payload.get("repository", {}).get("full_name", "?"),
            payload.get("sender", {}).get("login", "?"),
        )

        store.emit(
            "webhook_received",
            delivery_id=delivery_id,
            event=event,
            action=payload.get("action", ""),
            repo=payload.get("repository", {}).get("full_name", "?"),
        )
        logger.debug(
            "Webhook headers delivery_id=%s content-length=%s content-type=%s",
            delivery_id,
            request.headers.get("content-length"),
            request.headers.get("content-type"),
        )

        prompt = build_orchestrator_prompt(
            delivery_id=delivery_id,
            event=event,
            payload=payload,
            max_payload_chars=cfg.max_payload_chars,
        )

        # Derive the project slug synchronously (cheap) and compute the project
        # settings.  The actual clone/sync happens in the background task so the
        # handler returns 202 immediately without blocking on git clone.
        project_slug = _derive_project_slug(payload)
        project_settings = replace(
            cfg,
            workspace=project_workspace_path(cfg.beads_workspace_root, project_slug),
        )
        repo_full = payload.get("repository", {}).get("full_name", "?")
        logger.info(
            "Project workspace delivery_id=%s project=%s repo=%s",
            delivery_id,
            project_slug,
            repo_full,
        )

        logger.info(
            "Prompt assembled delivery_id=%s prompt_chars=%d prompt_lines=%d",
            delivery_id,
            len(prompt),
            prompt.count("\n"),
        )
        logger.debug(
            "Prompt preview delivery_id=%s:\n%s", delivery_id, prompt[:500]
        )

        background_tasks.add_task(
            _safe_dispatch, project_settings, prompt, store, payload
        )

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
    app.include_router(
        create_dashboard_router(store, beads_loop, dashboard_token=cfg.dashboard_token)
    )
    app.include_router(create_dashboard_page_router(dashboard_token=cfg.dashboard_token))
    app.include_router(
        create_dashboard_pages_router(dashboard_token=cfg.dashboard_token)
    )

    return app
