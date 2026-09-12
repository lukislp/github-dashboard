"""Composition root: wires adapters to use cases based on Settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import httpx

from app.application.ports import (
    GitHubApi,
    GitHubOAuth,
    OverviewCache,
    SessionRepository,
    TokenCipher,
)
from app.application.use_cases import (
    AccessPolicy,
    CompleteLogin,
    GetOverview,
    Logout,
    ResolveSession,
)
from app.infrastructure.cache_memory import MemoryOverviewCache
from app.infrastructure.github_http import GitHubHttpApi, GitHubHttpOAuth
from app.infrastructure.session_sqlite import SqliteSessionRepository
from app.infrastructure.settings import Settings
from app.infrastructure.token_fernet import FernetTokenCipher
from app.web.security import CookieSigner


@dataclass(slots=True)
class Container:
    settings: Settings
    signer: CookieSigner
    oauth: GitHubOAuth
    api: GitHubApi
    sessions: SessionRepository
    cache: OverviewCache
    cipher: TokenCipher
    complete_login: CompleteLogin
    resolve_session: ResolveSession
    logout: Logout
    get_overview: GetOverview
    _closables: list[object]

    @classmethod
    def from_settings(cls, settings: Settings) -> Container:
        http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        closables: list[object] = [http]
        cipher = FernetTokenCipher(settings.secret_key)

        sessions: SessionRepository
        cache: OverviewCache
        if settings.redis_url:
            from redis.asyncio import Redis

            from app.infrastructure.session_redis import RedisOverviewCache, RedisSessionRepository

            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            closables.append(redis)
            sessions = RedisSessionRepository(redis)
            cache = RedisOverviewCache(redis)
        else:
            sqlite = SqliteSessionRepository(settings.db_path)
            closables.append(sqlite)
            sessions = sqlite
            cache = MemoryOverviewCache()

        oauth = GitHubHttpOAuth(
            http,
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
            callback_url=settings.callback_url,
            scopes=settings.github_scopes,
            api_url=settings.github_api_url,
            web_url=settings.github_web_url,
        )
        api = GitHubHttpApi(http, api_url=settings.github_api_url)
        return cls.assemble(
            settings, oauth=oauth, api=api, sessions=sessions, cache=cache, cipher=cipher
        )._with_closables(closables)

    @classmethod
    def assemble(
        cls,
        settings: Settings,
        *,
        oauth: GitHubOAuth,
        api: GitHubApi,
        sessions: SessionRepository,
        cache: OverviewCache,
        cipher: TokenCipher,
    ) -> Container:
        """Build the use cases from explicit adapters (used by tests with fakes)."""
        return cls(
            settings=settings,
            signer=CookieSigner(settings.secret_key),
            oauth=oauth,
            api=api,
            sessions=sessions,
            cache=cache,
            cipher=cipher,
            complete_login=CompleteLogin(
                oauth=oauth,
                sessions=sessions,
                cipher=cipher,
                policy=AccessPolicy(settings.allowed_logins),
                session_ttl=timedelta(hours=settings.session_ttl_hours),
            ),
            resolve_session=ResolveSession(sessions=sessions),
            logout=Logout(oauth=oauth, sessions=sessions, cipher=cipher, cache=cache),
            get_overview=GetOverview(
                api=api,
                sessions=sessions,
                cipher=cipher,
                cache=cache,
                cache_ttl_seconds=settings.cache_ttl_seconds,
                runs_per_repo=settings.runs_per_repo,
                max_concurrency=settings.max_concurrency,
            ),
            _closables=[],
        )

    def _with_closables(self, closables: list[object]) -> Container:
        self._closables = closables
        return self

    async def aclose(self) -> None:
        for item in self._closables:
            aclose = getattr(item, "aclose", None)
            if aclose is not None:
                await aclose()
                continue
            close = getattr(item, "close", None)
            if close is not None:
                close()
