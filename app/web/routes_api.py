"""JSON API consumed by the dashboard page. Every endpoint requires a session."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.application.errors import AuthenticationError, GitHubUnavailable, RateLimited
from app.domain.codec import overview_to_dict, user_to_dict
from app.web.deps import ContainerDep, SessionDep
from app.web.routes_auth import clear_session_cookie

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _unauthorized() -> JSONResponse:
    response = JSONResponse({"error": "unauthorized"}, status_code=401)
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(session: SessionDep) -> JSONResponse:
    if session is None:
        return _unauthorized()
    return JSONResponse({"user": user_to_dict(session.user)})


@router.get("/overview")
async def overview(
    container: ContainerDep, session: SessionDep, refresh: bool = False
) -> JSONResponse:
    if session is None:
        return _unauthorized()
    try:
        result = await container.get_overview(session, force_refresh=refresh)
    except AuthenticationError:
        return _unauthorized()
    except RateLimited:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except GitHubUnavailable:
        log.exception("overview failed user=%s", session.user.login)
        return JSONResponse({"error": "github_unavailable"}, status_code=502)

    body = overview_to_dict(result.overview)
    body["from_cache"] = result.from_cache
    body["cache_ttl_seconds"] = container.settings.cache_ttl_seconds
    body["user"] = user_to_dict(session.user)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
