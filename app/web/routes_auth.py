"""OAuth login, callback and logout."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.application.errors import AccessDenied, AuthenticationError, GitHubUnavailable
from app.web.deps import ContainerDep, SessionDep
from app.web.security import SESSION_COOKIE, STATE_COOKIE, STATE_MAX_AGE

log = logging.getLogger(__name__)
router = APIRouter()


def set_session_cookie(response: Response, container: ContainerDep, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        container.signer.sign_session(session_id),
        max_age=container.settings.session_ttl_hours * 3600,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/auth/github")
async def start_login(container: ContainerDep, session: SessionDep) -> Response:
    if session is not None:
        return RedirectResponse("/", status_code=303)
    state = container.signer.new_state()
    response = RedirectResponse(container.oauth.authorize_url(state), status_code=303)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_MAX_AGE,
        httponly=True,
        secure=container.settings.cookie_secure,
        samesite="lax",
        path="/auth",
    )
    return response


@router.get("/auth/callback")
async def finish_login(
    request: Request,
    container: ContainerDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    if error:
        log.info("oauth denied by user: %s", error)
        return _login_error("denied")
    if not container.signer.verify_state(request.cookies.get(STATE_COOKIE), state):
        return _login_error("state")
    if not code:
        return _login_error("code")

    try:
        record = await container.complete_login(code)
    except AccessDenied:
        return _login_error("forbidden")
    except AuthenticationError:
        return _login_error("code")
    except GitHubUnavailable:
        log.exception("login failed talking to GitHub")
        return _login_error("github")

    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    set_session_cookie(response, container, record.id)
    return response


@router.post("/logout")
async def logout(container: ContainerDep, session: SessionDep) -> Response:
    if session is not None:
        await container.logout(session)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


def _login_error(reason: str) -> Response:
    response = RedirectResponse(f"/login?error={reason}", status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    return response
