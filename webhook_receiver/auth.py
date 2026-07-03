from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

#: Cookie name shared by the dashboard and the simulator for token persistence.
TOKEN_COOKIE = "dashboard_token"


def _extract_token(request: Request) -> str | None:
    """Read the dashboard token from Bearer header, ``?token=`` query, or cookie."""
    provided: str | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        provided = auth_header.split(None, 1)[1].strip()
    if not provided:
        provided = request.query_params.get("token")
    if not provided:
        provided = request.cookies.get(TOKEN_COOKIE)
    return provided


def make_dashboard_token_dep(
    token: str | None,
    *,
    disabled_status: int = 404,
    disabled_detail: str = "Dashboard is disabled (DASHBOARD_TOKEN not set)",
):
    """Build a FastAPI dependency that gates a route behind a dashboard token.

    The token may be presented via an ``Authorization: Bearer <token>`` header,
    a ``?token=`` query parameter, or the ``dashboard_token`` cookie, compared
    in constant time.

    Callers select fail-closed behavior when no token is configured:

    * Dashboard routes pass the defaults (``404``, "disabled") so the surface
      stays hidden by default.
    * The simulator passes ``disabled_status=401`` with an actionable message,
      because ``WEBHOOK_ENABLE_SIMULATOR=1`` already opts in to the surface.
    """

    async def _require_token(request: Request) -> None:
        if not token:
            raise HTTPException(
                status_code=disabled_status, detail=disabled_detail
            )
        provided = _extract_token(request)
        if not provided or not hmac.compare_digest(str(provided), token):
            raise HTTPException(
                status_code=401, detail="Invalid or missing dashboard token"
            )

    return _require_token


def persist_token_cookie(response, request: Request, token: str | None) -> None:
    """Persist a valid ``?token=`` query param as the ``dashboard_token`` cookie.

    Lets subsequent same-origin ``fetch()``/``EventSource`` requests (and new
    tabs opened via ``window.open``) authenticate automatically. No-op when no
    token is configured or the provided token does not match.
    """
    query_token = request.query_params.get("token")
    if query_token and token and hmac.compare_digest(query_token, token):
        response.set_cookie(
            TOKEN_COOKIE,
            query_token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
