"""Ports: the interfaces the application layer needs. Implemented in infrastructure/."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.hygiene import HygieneFacts
from app.domain.models import (
    ActionsUsage,
    Branch,
    FailedJob,
    Inbox,
    Issue,
    Notification,
    Overview,
    Preferences,
    PullRequest,
    RateLimit,
    ReleaseInfo,
    Repository,
    RepoUsage,
    SeverityCounts,
    Snapshot,
    User,
    WorkflowRun,
)


@dataclass(frozen=True, slots=True)
class TokenSet:
    """Result of an OAuth code exchange or a token refresh.

    `expires_at`, `refresh_token` and `refresh_expires_at` are `None` when the OAuth App does
    not have "Expire user access tokens" enabled: GitHub then issues a non-expiring token and
    no refresh token at all. Both shapes must be handled.
    """

    access_token: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    refresh_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A signed-in user. The GitHub token is stored encrypted (see TokenCipher).

    `token_expires_at`, `refresh_token_ciphertext` and `refresh_expires_at` stay `None` for a
    non-expiring token (see `TokenSet`).
    """

    id: str
    user: User
    token_ciphertext: str
    created_at: datetime
    expires_at: datetime
    token_expires_at: datetime | None = None
    refresh_token_ciphertext: str | None = None
    refresh_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RepositoryPage:
    repositories: tuple[Repository, ...]
    rate_limit: RateLimit | None
    # The following two are populated from the same GraphQL query as `repositories`, keyed
    # by full name. `dependabot_by_repo` holds (severity counts, GraphQL totalCount), or
    # (None, None) when the field is unavailable for that repository. `release_by_repo` holds
    # the release info without `unreleased_commits`, which is filled in by a later REST call.
    dependabot_by_repo: Mapping[str, tuple[SeverityCounts | None, int | None]] = field(
        default_factory=dict
    )
    release_by_repo: Mapping[str, ReleaseInfo | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BranchListing:
    """A repository's branch count and its branches without a pull request."""

    branch_count: int
    branches: tuple[Branch, ...]


@dataclass(frozen=True, slots=True)
class HygienePage:
    """Result of one `fetch_hygiene` call, keyed by repository full name (`nameWithOwner`).

    A repository missing from either mapping means its hygiene batch (or the half of it it
    fell into after a retry) could not be fetched this refresh; the caller treats that as
    "hygiene not applicable / no branch data" rather than failing the whole overview.
    """

    hygiene_by_repo: Mapping[str, HygieneFacts] = field(default_factory=dict)
    branches_by_repo: Mapping[str, BranchListing] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepoItemPage:
    """One cursor-paginated page of a single repository's open pull requests or issues.

    Only one of `pull_requests`/`issues` is ever populated, depending on the `kind` requested
    from `GitHubApi.list_repo_items`.
    """

    pull_requests: tuple[PullRequest, ...] = ()
    issues: tuple[Issue, ...] = ()
    next_cursor: str | None = None


class GitHubOAuth(Protocol):
    def authorize_url(self, state: str) -> str: ...

    async def exchange_code(self, code: str) -> TokenSet:
        """Exchange the OAuth code for a token set."""
        ...

    async def fetch_viewer(self, token: str) -> User: ...

    async def revoke_token(self, token: str) -> None:
        """Best effort: invalidate the token at GitHub."""
        ...

    async def refresh_token(self, refresh_token: str) -> TokenSet:
        """Exchange a refresh token for a new token set (the old refresh token is invalidated
        by GitHub in the process).

        Raises `AuthenticationError` when GitHub rejects the refresh token (`bad_refresh_token`
        or a similar error, or a response with no `access_token`), `GitHubUnavailable` on a
        transport failure or a 5xx response."""
        ...


class GitHubApi(Protocol):
    async def list_repositories(self, token: str) -> RepositoryPage:
        """All repositories the token can see, with open PR/issue counts."""
        ...

    async def fetch_hygiene(self, token: str, repo_ids: Sequence[str]) -> HygienePage:
        """Hygiene facts and branch listings for the given repository ids.

        Fetched in batches (25 ids per GraphQL request) from a separate query so a slow or
        failing lookup here can never take down the rest of the overview. A repository whose
        batch could not be recovered (even after the retry-and-split policy) is simply absent
        from the result."""
        ...

    async def list_recent_runs(
        self, token: str, owner: str, name: str, limit: int
    ) -> list[WorkflowRun]:
        """Newest workflow runs of one repository, newest first."""
        ...

    async def search_inbox(self, token: str) -> Inbox:
        """Pull requests and issues waiting on the viewer: review requests, changes
        requested on their own PRs, assignments and mentions."""
        ...

    async def fetch_security(
        self, token: str, owner: str, name: str
    ) -> tuple[SeverityCounts | None, int | None]:
        """Open code-scanning alerts by severity, and the open secret-scanning alert count.

        Either part is `None` when that feature is disabled or the token cannot see it
        (GitHub answers 403/404)."""
        ...

    async def count_commits_since(
        self, token: str, owner: str, name: str, base: str, head: str
    ) -> int | None:
        """Commits on `head` that are not yet in `base`. `None` when the comparison fails
        (e.g. the base ref no longer exists)."""
        ...

    async def list_notifications(self, token: str) -> list[Notification] | None:
        """Unread notifications for the viewer. `None` when the `notifications` scope is
        missing (GitHub answers 403/404)."""
        ...

    async def list_failed_jobs(
        self, token: str, owner: str, name: str, run_id: int
    ) -> tuple[FailedJob, ...]:
        """Failed jobs of one workflow run, each with the first step that failed in it.

        Empty when the run has no failed jobs, or when the lookup itself is unavailable
        (404/403); callers must never treat that as an error."""
        ...

    async def rerun_failed_jobs(self, token: str, owner: str, name: str, run_id: int) -> None:
        """Re-run the failed jobs of one workflow run. The only write this app performs.

        Raises `AccessDenied` (403), `ActionsUnavailable` (404), `RunNotRerunnable` (409: the
        run is still in progress or too old to rerun) or `AuthenticationError` (401)."""
        ...

    async def list_repo_items(
        self, token: str, owner: str, name: str, kind: str, cursor: str | None, limit: int
    ) -> RepoItemPage:
        """One cursor-paginated page of one repository's open pull requests or issues.

        `kind` is `"pull_requests"` or `"issues"`."""
        ...

    async def fetch_actions_usage(self, token: str, login: str) -> ActionsUsage:
        """GitHub Actions billing usage for the signed-in user.

        `ActionsUsage.available` is False (all other fields `None`) when the endpoint answers
        403/404 - typically because the token's OAuth scopes do not include `user` - which must
        never be treated as an error."""
        ...

    async def list_run_durations(
        self, token: str, owner: str, name: str, since: datetime
    ) -> RepoUsage:
        """Observed wall-clock CI time of one repository's workflow runs created since `since`.

        Sums `updated_at - run_started_at` (falling back to `created_at`) over every run that
        is not still active; active runs contribute nothing. Follows `page=2` at most once (at
        most 200 runs), setting `truncated` when GitHub reports more via `total_count`. 404/403
        (Actions disabled, no permission) degrade to a zero `RepoUsage`, never an error."""
        ...


class SessionRepository(Protocol):
    async def create(self, record: SessionRecord) -> None: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def delete(self, session_id: str) -> None: ...

    async def purge_expired(self, now: datetime) -> int: ...

    async def update_tokens(
        self,
        session_id: str,
        *,
        token_ciphertext: str,
        token_expires_at: datetime | None,
        refresh_token_ciphertext: str | None,
        refresh_expires_at: datetime | None,
    ) -> None:
        """Replace a session's token material after a refresh. A no-op if the session is gone
        (e.g. raced with a logout)."""
        ...


class OverviewCache(Protocol):
    async def get(self, user_id: int) -> Overview | None: ...

    async def set(self, user_id: int, overview: Overview, ttl_seconds: int) -> None: ...

    async def invalidate(self, user_id: int) -> None: ...


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class UserStateRepository(Protocol):
    """Per-user state that survives logout: repo groups/favourites and the last Snapshot.

    Keyed by GitHub user id, not by session id.
    """

    async def get_preferences(self, user_id: int) -> Preferences: ...

    async def set_preferences(self, user_id: int, prefs: Preferences) -> None: ...

    async def get_snapshot(self, user_id: int) -> Snapshot | None: ...

    async def set_snapshot(self, user_id: int, snapshot: Snapshot) -> None: ...
