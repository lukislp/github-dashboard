"""Use cases. Orchestrate ports, contain no transport or storage details."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
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
    BranchListing,
    GitHubApi,
    GitHubOAuth,
    HygienePage,
    OverviewCache,
    SessionRecord,
    SessionRepository,
    TokenCipher,
    TokenSet,
    UserStateRepository,
)
from app.domain.hygiene import RepoHygiene, assess_hygiene
from app.domain.models import (
    Changes,
    Inbox,
    Notification,
    Overview,
    Preferences,
    ReleaseInfo,
    RepoCi,
    RepoSecurity,
    Repository,
    Snapshot,
)
from app.domain.overview import (
    DEFAULT_LONG_RUN_AFTER,
    DEFAULT_STALE_AFTER,
    build_overview,
    classify_ci,
    skipped_ci,
)
from app.domain.snapshot import diff_since, snapshot_of

_MAX_GROUPS = 30
_MAX_GROUP_NAME_LEN = 40
_MAX_REPOS_TOTAL = 500
_REPO_NAME_RE = re.compile(r"^[\w.-]+/[\w.-]+$")

log = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def _attach_branch_listing(repo: Repository, listing: BranchListing | None) -> Repository:
    """Attach a repository's branch count/branches-without-pr from the hygiene fetch.

    `listing` is `None` when the repository's hygiene batch could not be fetched this
    refresh (archived, disabled, or degraded); the repository then keeps its defaults
    (`branch_count=0`, `branches_without_pr=()`).
    """
    if listing is None:
        return repo
    return dataclasses.replace(
        repo, branch_count=listing.branch_count, branches_without_pr=listing.branches
    )


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
        token_set = await self.oauth.exchange_code(code)
        user = await self.oauth.fetch_viewer(token_set.access_token)
        if not self.policy.allows(user.login):
            await self.oauth.revoke_token(token_set.access_token)
            raise AccessDenied(user.login)
        now = self.clock()
        refresh_ciphertext = (
            self.cipher.encrypt(token_set.refresh_token) if token_set.refresh_token else None
        )
        record = SessionRecord(
            id=secrets.token_urlsafe(32),
            user=user,
            token_ciphertext=self.cipher.encrypt(token_set.access_token),
            created_at=now,
            expires_at=now + self.session_ttl,
            token_expires_at=token_set.expires_at,
            refresh_token_ciphertext=refresh_ciphertext,
            refresh_expires_at=token_set.refresh_expires_at,
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


@dataclass(slots=True)
class EnsureFreshToken:
    """Returns a usable access token for a session, refreshing it first when needed.

    Only relevant when the OAuth App has "Expire user access tokens" enabled
    (`session.token_expires_at` is not `None`); otherwise the token never expires and is
    returned unchanged. A token that is not yet within `leeway` of expiring is also returned
    unchanged. Otherwise:

    - if a refresh token is on file and it has not itself expired, it is exchanged for a new
      token set at GitHub, persisted via `SessionRepository.update_tokens`, and the updated
      session plus the new plaintext access token are returned;
    - if the refresh itself is rejected by GitHub (`AuthenticationError`), the session is
      deleted and the error re-raised;
    - otherwise (no refresh token, or it has expired) the session is deleted and
      `AuthenticationError` is raised, exactly as an outright revoked token would.
    """

    oauth: GitHubOAuth
    sessions: SessionRepository
    cipher: TokenCipher
    clock: Clock = utc_now
    leeway: timedelta = timedelta(minutes=5)

    async def __call__(self, session: SessionRecord) -> tuple[SessionRecord, str]:
        expires_at = session.token_expires_at
        if expires_at is None or expires_at - self.clock() > self.leeway:
            return session, self.cipher.decrypt(session.token_ciphertext)

        refresh_ciphertext = session.refresh_token_ciphertext
        refresh_expired = (
            session.refresh_expires_at is not None and session.refresh_expires_at <= self.clock()
        )
        if refresh_ciphertext is None or refresh_expired:
            await self.sessions.delete(session.id)
            raise AuthenticationError("access token expired, no usable refresh token")

        refresh_token = self.cipher.decrypt(refresh_ciphertext)
        try:
            token_set: TokenSet = await self.oauth.refresh_token(refresh_token)
        except AuthenticationError:
            await self.sessions.delete(session.id)
            raise

        new_token_ciphertext = self.cipher.encrypt(token_set.access_token)
        new_refresh_ciphertext = (
            self.cipher.encrypt(token_set.refresh_token) if token_set.refresh_token else None
        )
        await self.sessions.update_tokens(
            session.id,
            token_ciphertext=new_token_ciphertext,
            token_expires_at=token_set.expires_at,
            refresh_token_ciphertext=new_refresh_ciphertext,
            refresh_expires_at=token_set.refresh_expires_at,
        )
        updated_session = dataclasses.replace(
            session,
            token_ciphertext=new_token_ciphertext,
            token_expires_at=token_set.expires_at,
            refresh_token_ciphertext=new_refresh_ciphertext,
            refresh_expires_at=token_set.refresh_expires_at,
        )
        return updated_session, token_set.access_token


@dataclass(frozen=True, slots=True)
class OverviewResult:
    overview: Overview
    from_cache: bool


@dataclass(slots=True)
class GetOverview:
    api: GitHubApi
    sessions: SessionRepository
    ensure_fresh_token: EnsureFreshToken
    cache: OverviewCache
    cache_ttl_seconds: int
    runs_per_repo: int
    max_concurrency: int
    stale_after: timedelta = DEFAULT_STALE_AFTER
    long_run_after: timedelta = DEFAULT_LONG_RUN_AFTER
    security_alerts: bool = True
    hygiene_checks: bool = True
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
        session, token = await self.ensure_fresh_token(session)
        page = await self.api.list_repositories(token)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def worker(repo: Repository) -> tuple[str, RepoCi, RepoSecurity, ReleaseInfo | None]:
            dependabot, dependabot_total = page.dependabot_by_repo.get(repo.full_name, (None, None))
            partial_release = page.release_by_repo.get(repo.full_name)

            if repo.is_archived:
                security = RepoSecurity(dependabot, dependabot_total, None, None)
                return repo.full_name, skipped_ci("archived"), security, partial_release

            async with semaphore:
                try:
                    runs = await self.api.list_recent_runs(
                        token, repo.owner, repo.name, self.runs_per_repo
                    )
                    ci = classify_ci(runs)
                except AuthenticationError:
                    raise
                except ActionsUnavailable as exc:
                    ci = classify_ci((), error=str(exc) or "unavailable")
                except RateLimited:
                    ci = classify_ci((), error="rate_limited")
                except GitHubUnavailable as exc:
                    log.warning("runs unavailable repo=%s: %s", repo.full_name, exc)
                    ci = classify_ci((), error="github_error")

                if self.security_alerts:
                    try:
                        code_scanning, secret_scanning = await self.api.fetch_security(
                            token, repo.owner, repo.name
                        )
                    except AuthenticationError:
                        raise
                    except (RateLimited, GitHubUnavailable) as exc:
                        log.warning("security fetch failed repo=%s: %s", repo.full_name, exc)
                        code_scanning, secret_scanning = None, None
                else:
                    code_scanning, secret_scanning = None, None
                security = RepoSecurity(
                    dependabot, dependabot_total, code_scanning, secret_scanning
                )

                release = partial_release
                if partial_release is not None and repo.default_branch:
                    try:
                        ahead_by = await self.api.count_commits_since(
                            token,
                            repo.owner,
                            repo.name,
                            partial_release.tag,
                            repo.default_branch,
                        )
                    except AuthenticationError:
                        raise
                    except (RateLimited, GitHubUnavailable) as exc:
                        log.warning("release compare failed repo=%s: %s", repo.full_name, exc)
                        ahead_by = None
                    release = dataclasses.replace(partial_release, unreleased_commits=ahead_by)

            return repo.full_name, ci, security, release

        async def inbox() -> Inbox:
            try:
                return await self.api.search_inbox(token)
            except (RateLimited, GitHubUnavailable) as exc:
                log.warning("inbox search failed: %s", exc)
                return Inbox((), (), (), ())

        async def notifications() -> tuple[tuple[Notification, ...], bool]:
            try:
                result = await self.api.list_notifications(token)
            except (RateLimited, GitHubUnavailable) as exc:
                log.warning("notifications fetch failed: %s", exc)
                return (), False
            if result is None:
                return (), False
            return tuple(result), True

        async def hygiene() -> HygienePage:
            if not self.hygiene_checks:
                return HygienePage({}, {})
            repo_ids = [r.node_id for r in page.repositories if not r.is_archived]
            if not repo_ids:
                return HygienePage({}, {})
            # Runs alongside the per-repo REST work, under the same concurrency limit.
            # AuthenticationError and RateLimited propagate like everywhere else; any other
            # failure (GitHubUnavailable) must never take the whole overview down, so it
            # degrades to "no hygiene/branch data this refresh" instead.
            async with semaphore:
                try:
                    return await self.api.fetch_hygiene(token, repo_ids)
                except GitHubUnavailable as exc:
                    log.warning("hygiene fetch failed: %s", exc)
                    return HygienePage({}, {})

        (
            worker_results,
            inbox_result,
            (notification_items, notifications_available),
            hygiene_page,
        ) = await asyncio.gather(
            asyncio.gather(*(worker(r) for r in page.repositories)),
            inbox(),
            notifications(),
            hygiene(),
        )
        ci_by_repo: dict[str, RepoCi] = {}
        security_by_repo: dict[str, RepoSecurity] = {}
        release_by_repo: dict[str, ReleaseInfo | None] = {}
        for full_name, ci, security, release in worker_results:
            ci_by_repo[full_name] = ci
            security_by_repo[full_name] = security
            release_by_repo[full_name] = release

        repositories = tuple(
            _attach_branch_listing(repo, hygiene_page.branches_by_repo.get(repo.full_name))
            for repo in page.repositories
        )
        hygiene_by_repo: dict[str, RepoHygiene] = {
            full_name: assess_hygiene(facts)
            for full_name, facts in hygiene_page.hygiene_by_repo.items()
        }

        return build_overview(
            viewer_login=session.user.login,
            repositories=repositories,
            ci_by_repo=ci_by_repo,
            rate_limit=page.rate_limit,
            inbox=inbox_result,
            security_by_repo=security_by_repo,
            release_by_repo=release_by_repo,
            hygiene_by_repo=hygiene_by_repo,
            notifications=notification_items,
            notifications_available=notifications_available,
            stale_after=self.stale_after,
            long_run_after=self.long_run_after,
            now=self.clock(),
        )


def validate_preferences(prefs: Preferences) -> None:
    """Raise ValueError when `prefs` violates a stored-state limit.

    Limits: at most 30 groups, group names non-empty/at most 40 chars/unique
    case-insensitively, at most 500 repository names in total (groups plus favourites), and
    every repository name must look like `owner/repo`.
    """
    if len(prefs.groups) > _MAX_GROUPS:
        raise ValueError(f"a maximum of {_MAX_GROUPS} groups is allowed")

    seen_names: set[str] = set()
    total_repos = len(prefs.favorites)
    for group in prefs.groups:
        if not group.name.strip():
            raise ValueError("group name must not be empty")
        if len(group.name) > _MAX_GROUP_NAME_LEN:
            raise ValueError(f"group name too long: {group.name!r}")
        key = group.name.casefold()
        if key in seen_names:
            raise ValueError(f"duplicate group name: {group.name!r}")
        seen_names.add(key)
        total_repos += len(group.repos)

    if total_repos > _MAX_REPOS_TOTAL:
        raise ValueError(f"a maximum of {_MAX_REPOS_TOTAL} repositories is allowed")

    all_repos = (*prefs.favorites, *(repo for group in prefs.groups for repo in group.repos))
    for repo_name in all_repos:
        if not _REPO_NAME_RE.match(repo_name):
            raise ValueError(f"invalid repository name: {repo_name!r}")


@dataclass(slots=True)
class GetPreferences:
    user_state: UserStateRepository

    async def __call__(self, session: SessionRecord) -> Preferences:
        return await self.user_state.get_preferences(session.user.id)


@dataclass(slots=True)
class SavePreferences:
    user_state: UserStateRepository

    async def __call__(self, session: SessionRecord, prefs: Preferences) -> None:
        validate_preferences(prefs)
        await self.user_state.set_preferences(session.user.id, prefs)


@dataclass(slots=True)
class MarkSeen:
    """Records what the viewer has now seen, for a later `GetChanges` to diff against."""

    user_state: UserStateRepository
    clock: Clock = utc_now

    async def __call__(self, session: SessionRecord, overview: Overview) -> Snapshot:
        snapshot = snapshot_of(overview, self.clock())
        await self.user_state.set_snapshot(session.user.id, snapshot)
        return snapshot


@dataclass(slots=True)
class GetChanges:
    user_state: UserStateRepository

    async def __call__(self, session: SessionRecord, overview: Overview) -> Changes:
        snapshot = await self.user_state.get_snapshot(session.user.id)
        return diff_since(overview, snapshot)
