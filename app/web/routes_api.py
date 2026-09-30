"""JSON API consumed by the dashboard page. Every endpoint requires a session."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.application.errors import (
    AccessDenied,
    ActionsUnavailable,
    AuthenticationError,
    GitHubUnavailable,
    PreferencesInvalid,
    RateLimited,
    RunNotRerunnable,
)
from app.application.ports import SessionRecord
from app.application.use_cases import OverviewResult
from app.domain.codec import (
    changes_to_dict,
    issue_to_dict,
    overview_to_dict,
    pr_to_dict,
    preferences_from_dict,
    preferences_to_dict,
    user_to_dict,
)
from app.domain.models import Preferences
from app.web.container import Container
from app.web.deps import ContainerDep, SameOriginDep, SessionDep
from app.web.routes_auth import clear_session_cookie

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# Seconds of silence on the overview stream after which a comment line is sent, so a proxy
# between the browser and this process never mistakes a long GitHub query for a dead
# connection (nginx and Cloudflare both cut idle upstreams well after this).
_STREAM_HEARTBEAT_SECONDS = 15

# Same shape GitHub allows for owner/repo path segments: letters, digits, `.`, `_`, `-`.
_OWNER_OR_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ITEM_KINDS = frozenset({"pull_requests", "issues"})


def _unauthorized() -> JSONResponse:
    response = JSONResponse({"error": "unauthorized"}, status_code=401)
    clear_session_cookie(response)
    return response


def _invalid_repository() -> JSONResponse:
    return JSONResponse({"error": "invalid_repository"}, status_code=400)


@router.get("/me")
async def me(session: SessionDep) -> JSONResponse:
    if session is None:
        return _unauthorized()
    return JSONResponse({"user": user_to_dict(session.user)})


@router.get("/overview")
async def overview(
    container: ContainerDep,
    session: SessionDep,
    refresh: bool = False,
    progressive: bool = False,
) -> JSONResponse:
    """The viewer's overview.

    `refresh=1` forces a new fetch from GitHub instead of serving the cache. `progressive=1`
    answers right away with whatever the current refresh has delivered so far - `pending`
    in the body lists the sections still missing (an empty list means complete), and the
    client polls again until it is empty. Without it the request blocks until the overview
    is complete. Either way, when a refresh fails outright the last complete overview is
    served with `stale_reason` set (and `from_cache` true) rather than an error, when there
    is one.
    """
    if session is None:
        return _unauthorized()
    try:
        result = await container.get_overview(session, force_refresh=refresh, wait=not progressive)
    except AuthenticationError:
        return _unauthorized()
    except RateLimited:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except GitHubUnavailable:
        log.exception("overview failed user=%s", session.user.login)
        return JSONResponse({"error": "github_unavailable"}, status_code=502)

    preferences = await container.get_preferences(session)
    body = await _overview_body(container, session, result, preferences)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


async def _overview_body(
    container: Container,
    session: SessionRecord,
    result: OverviewResult,
    preferences: Preferences,
) -> dict[str, Any]:
    changes = await container.get_changes(session, result.overview)
    body = overview_to_dict(result.overview)
    body["from_cache"] = result.from_cache
    body["cache_ttl_seconds"] = container.settings.cache_ttl_seconds
    body["stale_days"] = container.settings.stale_days
    body["user"] = user_to_dict(session.user)
    body["preferences"] = preferences_to_dict(preferences)
    body["changes"] = changes_to_dict(changes)
    return body


def _sse(event: str, data: dict[str, Any]) -> str:
    """One server-sent event. JSON never contains a raw newline, so one `data:` line is enough."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _with_heartbeat(events: AsyncIterator[str], interval: float) -> AsyncIterator[str]:
    """Pass `events` through, inserting an SSE comment whenever `interval` seconds pass
    without one. Cancels the pending read when the consumer goes away."""
    pending: asyncio.Future[str] | None = None
    try:
        while True:
            pending = asyncio.ensure_future(anext(events))
            while True:
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if done:
                    break
                yield ": ping\n\n"
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


@router.get("/overview/stream", response_model=None)
async def overview_stream(
    container: ContainerDep, session: SessionDep, refresh: bool = False
) -> Response:
    """The overview as server-sent events, one `overview` event per state the running refresh
    publishes (same body as `GET /api/overview`), the moment it is published - so a part
    that has come back from GitHub is on screen right away instead of at the next poll.

    Ends with `done` after the complete overview (or the stale fallback), or with a `failed`
    event carrying `{"error": "unauthorized" | "rate_limited" | "github_unavailable"}` and then
    `done`. `refresh=1` forces a new fetch, as for `GET /api/overview`. A comment line is sent
    every `_STREAM_HEARTBEAT_SECONDS` of silence.
    """
    if session is None:
        return _unauthorized()
    preferences = await container.get_preferences(session)

    async def events() -> AsyncIterator[str]:
        try:
            async for result in container.get_overview.stream(session, force_refresh=refresh):
                yield _sse(
                    "overview", await _overview_body(container, session, result, preferences)
                )
        except AuthenticationError:
            yield _sse("failed", {"error": "unauthorized"})
        except RateLimited:
            yield _sse("failed", {"error": "rate_limited"})
        except GitHubUnavailable:
            log.exception("overview stream failed user=%s", session.user.login)
            yield _sse("failed", {"error": "github_unavailable"})
        yield _sse("done", {})

    return StreamingResponse(
        _with_heartbeat(events(), _STREAM_HEARTBEAT_SECONDS),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


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
    # Parsing is kept out of the block below on purpose: a malformed body raises
    # json.JSONDecodeError, a ValueError whose text quotes byte offsets and payload excerpts.
    # Returning that verbatim is what CodeQL flags as py/stack-trace-exposure.
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    try:
        prefs = preferences_from_dict(body)
        await container.save_preferences(session, prefs)
    except PreferencesInvalid as exc:
        # `detail` is a message this application authored for the client, never exception text.
        return JSONResponse({"error": "invalid_preferences", "detail": exc.detail}, status_code=400)
    except (ValueError, TypeError, AttributeError, KeyError):
        # Anything the codec chokes on (wrong shape, wrong types) - the reason goes to the log,
        # the client only learns that the payload was rejected.
        log.exception("preferences rejected user=%s", session.user.login)
        return JSONResponse({"error": "invalid_preferences"}, status_code=400)
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


@router.post("/repos/{owner}/{name}/runs/{run_id}/rerun")
async def rerun_run(
    owner: str,
    name: str,
    run_id: int,
    container: ContainerDep,
    session: SessionDep,
    _csrf: SameOriginDep,
) -> JSONResponse:
    if session is None:
        return _unauthorized()
    if not _OWNER_OR_NAME_RE.match(owner) or not _OWNER_OR_NAME_RE.match(name):
        return _invalid_repository()
    try:
        await container.rerun_failed_jobs(session, owner, name, run_id)
    except AuthenticationError:
        return _unauthorized()
    except AccessDenied:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    except ActionsUnavailable:
        return JSONResponse({"error": "actions_unavailable"}, status_code=404)
    except RunNotRerunnable:
        return JSONResponse({"error": "not_rerunnable"}, status_code=409)
    except (RateLimited, GitHubUnavailable):
        log.exception(
            "rerun failed user=%s repo=%s/%s run_id=%s", session.user.login, owner, name, run_id
        )
        return JSONResponse({"error": "github_unavailable"}, status_code=502)
    return JSONResponse({"status": "queued"}, status_code=202)


@router.get("/repos/{owner}/{name}/items")
async def list_items(
    owner: str,
    name: str,
    kind: str,
    container: ContainerDep,
    session: SessionDep,
    cursor: str | None = None,
) -> JSONResponse:
    if session is None:
        return _unauthorized()
    if not _OWNER_OR_NAME_RE.match(owner) or not _OWNER_OR_NAME_RE.match(name):
        return _invalid_repository()
    if kind not in _ITEM_KINDS:
        return JSONResponse({"error": "invalid_kind"}, status_code=400)
    try:
        page = await container.list_repo_items(session, owner, name, kind, cursor)
    except AuthenticationError:
        return _unauthorized()
    except RateLimited:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except GitHubUnavailable:
        log.exception("items failed user=%s repo=%s/%s", session.user.login, owner, name)
        return JSONResponse({"error": "github_unavailable"}, status_code=502)

    return JSONResponse(
        {
            "pull_requests": [pr_to_dict(p) for p in page.pull_requests],
            "issues": [issue_to_dict(i) for i in page.issues],
            "next_cursor": page.next_cursor,
        }
    )
