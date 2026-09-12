"""Use cases. Orchestrate ports, contain no transport or storage details."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.application.errors import (
    AccessDenied,
    ActionsUnavailable,
    AuthenticationError,
    GitHubUnavailable,
    RateLimited,
)
from app.application.ports import (
    GitHubApi,
    GitHubOAuth,
    OverviewCache,
    SessionRecord,
    SessionRepository,
    TokenCipher,
)
from app.domain.models import Overview, RepoCi, Repository
from app.domain.overview import build_overview, classify_ci, skipped_ci

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Who may sign in. An empty allow-list means every GitHub account."""

    allowed_logins: frozenset[str] = frozenset()

    def allows(self, login: str) -> bool:
        return not self.allowed_logins or login.lower() in self.allowed_logins


@dataclass(slots=True)
class CompleteLogin:
    oauth: GitHubOAuth
    sessions: SessionRepository
    cipher: TokenCipher
    policy: AccessPolicy
    session_ttl: timedelta
    clock: Clock = utc_now

    async def __call__(self, code: str) -> SessionRecord:
        token = await self.oauth.exchange_code(code)
        user = await self.oauth.fetch_viewer(token)
        if not self.policy.allows(user.login):
            await self.oauth.revoke_token(token)
            raise AccessDenied(user.login)
        now = self.clock()
        record = SessionRecord(
            id=secrets.token_urlsafe(32),
            user=user,
            token_ciphertext=self.cipher.encrypt(token),
            created_at=now,
            expires_at=now + self.session_ttl,
        )
        await self.sessions.create(record)
        log.info("login user=%s", user.login)
        return record


@dataclass(slots=True)
class ResolveSession:
    sessions: SessionRepository
    clock: Clock = utc_now

    async def __call__(self, session_id: str | None) -> SessionRecord | None:
        if not session_id:
            return None
        record = await self.sessions.get(session_id)
        if record is None:
            return None
        if record.expires_at <= self.clock():
            await self.sessions.delete(session_id)
            return None
        return record


@dataclass(slots=True)
class Logout:
    oauth: GitHubOAuth
    sessions: SessionRepository
    cipher: TokenCipher
    cache: OverviewCache

    async def __call__(self, session: SessionRecord) -> None:
        await self.sessions.delete(session.id)
        await self.cache.invalidate(session.user.id)
        try:
            await self.oauth.revoke_token(self.cipher.decrypt(session.token_ciphertext))
        except Exception:  # noqa: BLE001 - revocation is best effort
            log.warning("token revocation failed user=%s", session.user.login, exc_info=True)
        log.info("logout user=%s", session.user.login)


@dataclass(frozen=True, slots=True)
class OverviewResult:
    overview: Overview
    from_cache: bool


@dataclass(slots=True)
class GetOverview:
    api: GitHubApi
    sessions: SessionRepository
    cipher: TokenCipher
    cache: OverviewCache
    cache_ttl_seconds: int
    runs_per_repo: int
    max_concurrency: int
    clock: Clock = utc_now
    _locks: dict[int, asyncio.Lock] = field(default_factory=dict)

    async def __call__(
        self, session: SessionRecord, *, force_refresh: bool = False
    ) -> OverviewResult:
        user_id = session.user.id
        if not force_refresh:
            cached = await self.cache.get(user_id)
            if cached is not None:
                return OverviewResult(cached, from_cache=True)

        # One in-flight refresh per user in this process; concurrent callers share it.
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            if not force_refresh:
                cached = await self.cache.get(user_id)
                if cached is not None:
                    return OverviewResult(cached, from_cache=True)
            try:
                overview = await self._load(session)
            except AuthenticationError:
                await self.sessions.delete(session.id)
                await self.cache.invalidate(user_id)
                raise
            await self.cache.set(user_id, overview, self.cache_ttl_seconds)
            return OverviewResult(overview, from_cache=False)

    async def _load(self, session: SessionRecord) -> Overview:
        token = self.cipher.decrypt(session.token_ciphertext)
        page = await self.api.list_repositories(token)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def ci_for(repo: Repository) -> tuple[str, RepoCi]:
            if repo.is_archived:
                return repo.full_name, skipped_ci("archived")
            async with semaphore:
                try:
                    runs = await self.api.list_recent_runs(
                        token, repo.owner, repo.name, self.runs_per_repo
                    )
                except AuthenticationError:
                    raise
                except ActionsUnavailable as exc:
                    return repo.full_name, classify_ci((), error=str(exc) or "unavailable")
                except RateLimited:
                    return repo.full_name, classify_ci((), error="rate_limited")
                except GitHubUnavailable as exc:
                    log.warning("runs unavailable repo=%s: %s", repo.full_name, exc)
                    return repo.full_name, classify_ci((), error="github_error")
            return repo.full_name, classify_ci(runs)

        results = await asyncio.gather(*(ci_for(r) for r in page.repositories))
        return build_overview(
            viewer_login=session.user.login,
            repositories=page.repositories,
            ci_by_repo=dict(results),
            rate_limit=page.rate_limit,
            now=self.clock(),
        )
