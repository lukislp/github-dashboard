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
