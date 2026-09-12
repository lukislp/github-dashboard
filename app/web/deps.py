"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.application.ports import SessionRecord
from app.web.container import Container
from app.web.security import SESSION_COOKIE


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_session(request: Request, container: ContainerDep) -> SessionRecord | None:
    session_id = container.signer.unsign_session(request.cookies.get(SESSION_COOKIE))
    return await container.resolve_session(session_id)


SessionDep = Annotated[SessionRecord | None, Depends(get_session)]


class CrossSiteRequest(Exception):
    """Raised by `require_same_origin` when a state-changing request looks cross-site."""


def require_same_origin(request: Request, container: ContainerDep) -> None:
    """CSRF guard for state-changing JSON endpoints (cookie auth has no built-in origin check).

    Modern browsers send `Sec-Fetch-Site` on every request; `same-origin`/`none` is safe,
    anything else (`cross-site`, `same-site`) is rejected. Older browsers that omit it are
    checked against `Origin` instead. A request with neither header (curl, our own tests) is
    allowed through: there is no ambient browser cookie jar to abuse.
    """
    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site is not None:
        if sec_fetch_site not in ("same-origin", "none"):
            raise CrossSiteRequest()
        return
    origin = request.headers.get("origin")
    if origin is not None and origin != container.settings.base_url:
        raise CrossSiteRequest()


SameOriginDep = Annotated[None, Depends(require_same_origin)]
