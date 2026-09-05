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
from webhook_receiver.filters import should_dispatch
from webhook_receiver.github import verify_signature
from webhook_receiver.prompts import build_orchestrator_prompt
from webhook_receiver.runner import DispatchContext, dispatch_to_opencode
from webhook_receiver.simulator import create_simulator_router
from webhook_receiver.webhook_store import WebhookStore
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

# Safe default-branch name from webhook payloads. Rejects values that could be
# parsed as git flags (leading ``-``) or contain path traversal; falls back to
# ``"main"`` so a malformed/missing payload never breaks clone/sync.
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _safe_branch(value: Any) -> str:
    """Return a git-safe branch name from *value*, or ``"main"``.

    The webhook is signature-validated so ``default_branch`` is trusted, but
    this guards against flag-injection via ``git clone --branch <x>`` /
    ``git checkout <x>`` and keeps non-``main`` repos working when the field is
    absent or malformed.
    """
    if not isinstance(value, str):
        return "main"
    branch = value.strip()
    if (
        not branch
        or branch.startswith("-")
        or ".." in branch
        or not _BRANCH_RE.match(branch)
    ):
        return "main"
    return branch


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
    default_branch = _safe_branch(repo.get("default_branch"))

    project_root = project_workspace_path(base, slug)
    if _validate_clone_url(clone_url):
        ensure_project_from_clone(base, slug, clone_url, base_branch=default_branch)
        sync_project(project_root, branch=default_branch)

    project_settings = replace(cfg, workspace=project_root)
    return slug, project_settings


def _safe_dispatch(
    settings: Settings,
    prompt: str,
    store: EventStore,
    payload: dict[str, Any],
    webhook_store: WebhookStore | None = None,
    delivery_id: str = "",
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
        repo_info = payload.get("repository", {})
        clone_url = repo_info.get("clone_url", "")
        # Thread the repo's default branch (master/develop/...) into clone/sync
        # so repos that do not default to "main" clone and pull correctly.
        default_branch = _safe_branch(repo_info.get("default_branch"))
        resolved = project_workspace_path(base, slug)
        if os.path.realpath(resolved) == os.path.realpath(base):
            logger.error(
                "Refusing to dispatch to workspace root base=%s slug=%r",
                base,
                slug,
            )
            return
        if _validate_clone_url(clone_url):
            ensure_project_from_clone(
                base, slug, clone_url, base_branch=default_branch
            )
            sync_project(resolved, branch=default_branch)
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
    dispatch_ctx = _dispatch_context_from_payload(payload)
    prompt_stem = dispatch_to_opencode(settings, prompt, store, dispatch_ctx)
    if webhook_store and delivery_id and isinstance(prompt_stem, str):
        webhook_store.record(
            delivery_id, decision="allowed", prompt_stem=prompt_stem
        )


def _dispatch_context_from_payload(
    payload: dict[str, Any]
) -> DispatchContext | None:
    """Build a DispatchContext for the failure-comment path.

    Returns None when the payload carries no attributable issue (e.g. a
    non-issue event), in which case no failure comment is posted.
    """
    repo_full = payload.get("repository", {}).get("full_name")
    issue = payload.get("issue", {})
    number = issue.get("number")
    if not repo_full or not isinstance(number, int):
        return None
    # The label that triggered this dispatch (``issues.labeled``). Used by the
    # completion watcher to gate the close-on-success incomplete check to the
    # clauses that close the issue on success (``orchestration:dispatch`` and
    # ``gh-issue-tracking:direct-body``); other labels don't close the issue.
    trigger_label = str((payload.get("label") or {}).get("name") or "").strip() or None
    return DispatchContext(
        repo_full_name=repo_full,
        issue_number=number,
        html_url=issue.get("html_url"),
        trigger_label=trigger_label,
    )


def create_app(
    settings: Settings | None = None,
    event_store: EventStore | None = None,
    beads_loop: BeadsLoop | None = None,
    webhook_store: WebhookStore | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = event_store or EventStore()
    wh_store = webhook_store or WebhookStore(cfg.log_dir)
    app = FastAPI(
        title="Orchestrator GitHub Webhook Receiver",
        version="0.1.0",
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/webhooks/github",
        status_code=202,
        responses={
            200: {"description": "Ping event acknowledged (pong)."},
            202: {
                "description": (
                    "Webhook delivery accepted and dispatched, or filtered/ignored "
                    "without dispatch (non-matching label, bot actor, etc.)."
                )
            },
            400: {"description": "Invalid JSON body."},
            401: {"description": "Invalid signature."},
            413: {"description": "Request body too large."},
        },
    )
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

        sender_login = payload.get("sender", {}).get("login", "?")
        repo_full_name = payload.get("repository", {}).get("full_name", "?")
        label_name = (payload.get("label") or {}).get("name", "")

        store.emit(
            "webhook_received",
            delivery_id=delivery_id,
            event=event,
            action=payload.get("action", ""),
            repo=repo_full_name,
        )
        wh_store.record(
            delivery_id,
            event=event,
            action=payload.get("action", ""),
            repo=repo_full_name,
            sender=sender_login,
            label=label_name,
        )
        logger.debug(
            "Webhook headers delivery_id=%s content-length=%s content-type=%s",
            delivery_id,
            request.headers.get("content-length"),
            request.headers.get("content-type"),
        )

        # Transport-level dispatch gate (mirrors orchestrator-agent.yml
        # orchestrate-job `if:`). Only ``issues.labeled`` by a non-bot actor
        # with a workflow-relevant label may spawn the agent; anything else is
        # acknowledged but not dispatched, preventing the echo-loop where the
        # prompt ``(default)`` clause posts a comment and re-triggers itself.
        allow, reason = should_dispatch(event, payload)
        if not allow:
            logger.info(
                "Filtered delivery_id=%s event=%s action=%s reason=%s",
                delivery_id,
                event,
                payload.get("action"),
                reason,
            )
            store.emit(
                "webhook_filtered",
                delivery_id=delivery_id,
                event=event,
                action=payload.get("action", ""),
                reason=reason,
            )
            wh_store.record(
                delivery_id,
                decision="denied",
                reason=reason,
            )
            return JSONResponse(
                {
                    "status": "ignored",
                    "delivery_id": delivery_id,
                    "event": event,
                    "reason": reason,
                },
                status_code=202,
            )

        wh_store.record(delivery_id, decision="allowed", reason=reason)

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
            _safe_dispatch,
            project_settings,
            prompt,
            store,
            payload,
            wh_store,
            delivery_id,
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

    app.include_router(
        create_simulator_router(
            enabled=cfg.enable_simulator,
            port=cfg.port,
            dashboard_token=cfg.dashboard_token,
        )
    )
    app.include_router(
        create_dashboard_router(
            store,
            beads_loop,
            dashboard_token=cfg.dashboard_token,
            log_dir=cfg.log_dir,
            webhook_store=wh_store,
        )
    )
    app.include_router(create_dashboard_page_router(dashboard_token=cfg.dashboard_token))
    app.include_router(
        create_dashboard_pages_router(dashboard_token=cfg.dashboard_token)
    )

    return app
