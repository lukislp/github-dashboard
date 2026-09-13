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
    UserStateRepository,
)
from app.application.use_cases import (
    AccessPolicy,
    CompleteLogin,
    GetChanges,
    GetOverview,
    GetPreferences,
    Logout,
    MarkSeen,
    ResolveSession,
    SavePreferences,
)
from app.infrastructure.cache_memory import MemoryOverviewCache
from app.infrastructure.github_http import GitHubHttpApi, GitHubHttpOAuth
from app.infrastructure.session_sqlite import SqliteSessionRepository
from app.infrastructure.settings import Settings
from app.infrastructure.token_fernet import FernetTokenCipher
from app.infrastructure.user_state_sqlite import SqliteUserStateRepository
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
    user_state: UserStateRepository
    complete_login: CompleteLogin
    resolve_session: ResolveSession
    logout: Logout
    get_overview: GetOverview
    get_preferences: GetPreferences
    save_preferences: SavePreferences
    mark_seen: MarkSeen
    get_changes: GetChanges
    _closables: list[object]

    @classmethod
    def from_settings(cls, settings: Settings) -> Container:
        http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        closables: list[object] = [http]
        cipher = FernetTokenCipher(settings.secret_key)

        sessions: SessionRepository
        cache: OverviewCache
        user_state: UserStateRepository
        if settings.redis_url:
            from redis.asyncio import Redis

            from app.infrastructure.session_redis import RedisOverviewCache, RedisSessionRepository
            from app.infrastructure.user_state_redis import RedisUserStateRepository

            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            closables.append(redis)
            sessions = RedisSessionRepository(redis)
            cache = RedisOverviewCache(redis)
            user_state = RedisUserStateRepository(redis)
        else:
            sqlite = SqliteSessionRepository(settings.db_path)
            closables.append(sqlite)
            sessions = sqlite
            cache = MemoryOverviewCache()
            user_state_sqlite = SqliteUserStateRepository(settings.db_path)
            closables.append(user_state_sqlite)
            user_state = user_state_sqlite

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
            settings,
            oauth=oauth,
            api=api,
            sessions=sessions,
            cache=cache,
            cipher=cipher,
            user_state=user_state,
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
        user_state: UserStateRepository,
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
            user_state=user_state,
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
                stale_after=timedelta(days=settings.stale_days),
                long_run_after=timedelta(minutes=settings.long_run_minutes),
                security_alerts=settings.security_alerts,
                hygiene_checks=settings.hygiene_checks,
            ),
            get_preferences=GetPreferences(user_state=user_state),
            save_preferences=SavePreferences(user_state=user_state),
            mark_seen=MarkSeen(user_state=user_state),
            get_changes=GetChanges(user_state=user_state),
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
