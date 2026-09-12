"""Ports: the interfaces the application layer needs. Implemented in infrastructure/."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.models import Inbox, Overview, RateLimit, Repository, User, WorkflowRun


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
