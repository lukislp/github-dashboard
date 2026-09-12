"""Ports: the interfaces the application layer needs. Implemented in infrastructure/."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.domain.models import (
    Inbox,
    Notification,
    Overview,
    Preferences,
    RateLimit,
    ReleaseInfo,
    Repository,
    SeverityCounts,
    Snapshot,
    User,
    WorkflowRun,
)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A signed-in user. The GitHub token is stored encrypted (see TokenCipher)."""

    id: str
    user: User
    token_ciphertext: str
    created_at: datetime
    expires_at: datetime


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


class GitHubOAuth(Protocol):
    def authorize_url(self, state: str) -> str: ...

    async def exchange_code(self, code: str) -> str:
        """Exchange the OAuth code for an access token."""
        ...

    async def fetch_viewer(self, token: str) -> User: ...

    async def revoke_token(self, token: str) -> None:
        """Best effort: invalidate the token at GitHub."""
        ...


class GitHubApi(Protocol):
    async def list_repositories(self, token: str) -> RepositoryPage:
        """All repositories the token can see, with open PR/issue counts."""
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


class SessionRepository(Protocol):
    async def create(self, record: SessionRecord) -> None: ...

    async def get(self, session_id: str) -> SessionRecord | None: ...

    async def delete(self, session_id: str) -> None: ...

    async def purge_expired(self, now: datetime) -> int: ...


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
