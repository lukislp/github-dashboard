"""ASGI entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.infrastructure.settings import Settings
from app.web import routes_api, routes_auth, routes_pages
from app.web.container import Container
from app.web.security import SecurityHeadersMiddleware

log = logging.getLogger(__name__)
_PURGE_INTERVAL_SECONDS = 3600


def create_app(container: Container | None = None) -> FastAPI:
    """Build the application. Pass a Container to inject fakes (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = container is None
        active = container or Container.from_settings(Settings.from_env())
        logging.basicConfig(level=active.settings.log_level)
        # httpx logs every request URL at INFO; keep that out of production logs.
        logging.getLogger("httpx").setLevel(logging.WARNING)
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
    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), "static"
    )
    app.include_router(routes_pages.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_api.router)
    return app


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
