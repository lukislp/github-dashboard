"""ASGI entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.infrastructure.settings import Settings
from app.web import routes_api, routes_auth, routes_pages
from app.web.container import Container
from app.web.deps import CrossSiteRequest
from app.web.security import SecurityHeadersMiddleware

log = logging.getLogger(__name__)
_PURGE_INTERVAL_SECONDS = 3600


class RedactOAuthCallbackQuery(logging.Filter):
    """Strips the query string of /auth/callback from uvicorn's access log.

    The one-time OAuth `code` and the `state` token are consumed the moment the request is
    handled, but they have no business sitting in log storage. uvicorn formats the access line
    from record.args = (client, method, path, http_version, status), so the path is replaced
    before formatting; every other request is logged unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) == 5
            and isinstance(args[2], str)
            and args[2].startswith("/auth/callback?")
        ):
            record.args = (*args[:2], "/auth/callback?<redacted>", *args[3:])
        return True


def create_app(container: Container | None = None) -> FastAPI:
    """Build the application. Pass a Container to inject fakes (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        active = container or Container.from_settings(Settings.from_env())
        logging.basicConfig(level=active.settings.log_level)
        # httpx logs every request URL at INFO; keep that out of production logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").addFilter(RedactOAuthCallbackQuery())
        app.state.container = active
        purge_task = asyncio.create_task(_purge_loop(active))
        try:
            yield
        finally:
            purge_task.cancel()
            if owned:
                await active.aclose()

    app = FastAPI(title="GitHub Dashboard", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_exception_handler(CrossSiteRequest, _cross_site_handler)
    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), "static"
    )
    app.include_router(routes_pages.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_api.router)
    return app


def _cross_site_handler(request: Request, exc: CrossSiteRequest) -> JSONResponse:
    return JSONResponse({"error": "cross_site"}, status_code=403)


async def _purge_loop(container: Container) -> None:
    while True:
        try:
            removed = await container.sessions.purge_expired(datetime.now(UTC))
            if removed:
                log.info("purged %d expired sessions", removed)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("session purge failed")
        await asyncio.sleep(_PURGE_INTERVAL_SECONDS)


app = create_app()
