"""JSON API consumed by the dashboard page. Every endpoint requires a session."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.application.errors import AuthenticationError, GitHubUnavailable, RateLimited
from app.domain.codec import (
    changes_to_dict,
    overview_to_dict,
    preferences_from_dict,
    preferences_to_dict,
    user_to_dict,
)
from app.web.deps import ContainerDep, SameOriginDep, SessionDep
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

    preferences = await container.get_preferences(session)
    changes = await container.get_changes(session, result.overview)

    body = overview_to_dict(result.overview)
    body["from_cache"] = result.from_cache
    body["cache_ttl_seconds"] = container.settings.cache_ttl_seconds
    body["user"] = user_to_dict(session.user)
    body["preferences"] = preferences_to_dict(preferences)
    body["changes"] = changes_to_dict(changes)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/preferences")
async def get_preferences(container: ContainerDep, session: SessionDep) -> JSONResponse:
    if session is None:
        return _unauthorized()
    prefs = await container.get_preferences(session)
    return JSONResponse(preferences_to_dict(prefs))


@router.put("/preferences")
async def put_preferences(
    request: Request, container: ContainerDep, session: SessionDep, _csrf: SameOriginDep
) -> JSONResponse:
    if session is None:
        return _unauthorized()
    try:
        body = await request.json()
        prefs = preferences_from_dict(body)
        await container.save_preferences(session, prefs)
    except ValueError as exc:
        return JSONResponse({"error": "invalid_preferences", "detail": str(exc)}, status_code=400)
    return JSONResponse(preferences_to_dict(prefs))


@router.post("/seen")
async def mark_seen(
    container: ContainerDep, session: SessionDep, _csrf: SameOriginDep
) -> JSONResponse:
    if session is None:
        return _unauthorized()
    try:
        result = await container.get_overview(session)
    except AuthenticationError:
        return _unauthorized()
    except RateLimited:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except GitHubUnavailable:
        log.exception("seen failed user=%s", session.user.login)
        return JSONResponse({"error": "github_unavailable"}, status_code=502)

    snapshot = await container.mark_seen(session, result.overview)
    return JSONResponse({"seen_at": snapshot.taken_at.isoformat()})
