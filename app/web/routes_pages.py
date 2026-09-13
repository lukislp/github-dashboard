"""HTML pages and health endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.web.deps import ContainerDep, SessionDep
from app.web.security import LANG_COOKIE, THEME_COOKIE

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_LOGIN_ERRORS = frozenset({"denied", "state", "code", "forbidden", "github", "expired"})


def _language(request: Request) -> str:
    cookie = request.cookies.get(LANG_COOKIE)
    if cookie in ("de", "en"):
        return cookie
    accept = request.headers.get("accept-language", "")
    return "de" if accept.lower().startswith("de") else "en"


def _theme(request: Request) -> str:
    cookie = request.cookies.get(THEME_COOKIE)
    return cookie if cookie in ("light", "dark") else "auto"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep) -> Response:
    if session is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"lang": _language(request), "theme": _theme(request), "user": session.user},
    )


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request, session: SessionDep, error: str | None = None) -> Response:
    if session is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "lang": _language(request),
            "theme": _theme(request),
            "error": error if error in _LOGIN_ERRORS else None,
        },
    )


@router.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.get("/readyz", include_in_schema=False)
async def readyz(container: ContainerDep) -> JSONResponse:
    # Touching the session store proves the storage backend is reachable.
    await container.sessions.get("readiness-probe")
    return JSONResponse({"status": "ready"})
