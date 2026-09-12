"""In-memory fakes for the application ports, shared by unit and integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.errors import ActionsUnavailable, AuthenticationError
from app.application.ports import RepositoryPage, SessionRecord
from app.domain.models import (
    Issue,
    Overview,
    PullRequest,
    RateLimit,
    Repository,
    RunStatus,
    User,
    WorkflowRun,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

DEFAULT_RATE_LIMIT = RateLimit(4990, 5000, NOW)

VIEWER = User(id=42, login="octocat", name="Octo Cat", avatar_url="https://a/x.png", html_url="h")


def make_repo(
    name: str,
    *,
    owner: str = "octocat",
    prs: int = 0,
    issues: int = 0,
    archived: bool = False,
    fork: bool = False,
    private: bool = False,
    stars: int = 0,
    pushed_at: datetime | None = NOW,
) -> Repository:
    return Repository(
        full_name=f"{owner}/{name}",
        name=name,
        owner=owner,
        url=f"https://github.com/{owner}/{name}",
        description=None,
        is_private=private,
        is_archived=archived,
        is_fork=fork,
        has_issues=True,
        stars=stars,
        pushed_at=pushed_at,
        language="Python",
        language_color="#3572A5",
        default_branch="main",
        open_pr_count=prs,
        open_issue_count=issues,
        pull_requests=tuple(
            PullRequest(i, f"PR {i}", f"u/{i}", "someone", False, NOW) for i in range(1, prs + 1)
        ),
        issues=tuple(
            Issue(i, f"Issue {i}", f"u/{i}", "someone", NOW) for i in range(1, issues + 1)
        ),
    )


def make_run(
    status: RunStatus,
    *,
    run_id: int = 1,
    workflow: str = "ci",
    branch: str = "main",
    age_minutes: int = 0,
) -> WorkflowRun:
    when = NOW - timedelta(minutes=age_minutes)
    return WorkflowRun(
        id=run_id,
        workflow_name=workflow,
        title=f"run {run_id}",
        url=f"https://github.com/x/y/actions/runs/{run_id}",
        branch=branch,
        event="push",
        status=status,
        run_number=run_id,
        created_at=when,
        updated_at=when,
    )


class FakeOAuth:
    def __init__(self, user: User = VIEWER, token: str = "gho_test") -> None:
        self.user = user
        self.token = token
        self.revoked: list[str] = []
        self.exchanged: list[str] = []
        self.reject_code = False

    def authorize_url(self, state: str) -> str:
        return f"https://github.example/login/oauth/authorize?state={state}"

    async def exchange_code(self, code: str) -> str:
        self.exchanged.append(code)
        if self.reject_code:
            raise AuthenticationError("bad_verification_code")
        return self.token

    async def fetch_viewer(self, token: str) -> User:
        if token != self.token:
            raise AuthenticationError("bad token")
        return self.user

    async def revoke_token(self, token: str) -> None:
        self.revoked.append(token)


class FakeApi:
    def __init__(
        self,
        repos: list[Repository] | None = None,
        runs: dict[str, list[WorkflowRun]] | None = None,
        rate_limit: RateLimit | None = DEFAULT_RATE_LIMIT,
    ) -> None:
        self.repos = repos or []
        self.runs = runs or {}
        self.rate_limit = rate_limit
        self.unavailable: set[str] = set()
        self.calls = 0
        self.run_calls: list[str] = []
        self.token_valid = True

    async def list_repositories(self, token: str) -> RepositoryPage:
        self.calls += 1
        if not self.token_valid:
            raise AuthenticationError("revoked")
        return RepositoryPage(tuple(self.repos), self.rate_limit)

    async def list_recent_runs(
        self, token: str, owner: str, name: str, limit: int
    ) -> list[WorkflowRun]:
        full = f"{owner}/{name}"
        self.run_calls.append(full)
        if full in self.unavailable:
            raise ActionsUnavailable("disabled")
        return self.runs.get(full, [])[:limit]


class FakeSessions:
    def __init__(self) -> None:
        self.records: dict[str, SessionRecord] = {}

    async def create(self, record: SessionRecord) -> None:
        self.records[record.id] = record

    async def get(self, session_id: str) -> SessionRecord | None:
        return self.records.get(session_id)

    async def delete(self, session_id: str) -> None:
        self.records.pop(session_id, None)

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, v in self.records.items() if v.expires_at <= now]
        for key in expired:
            del self.records[key]
        return len(expired)


class FakeCache:
    def __init__(self) -> None:
        self.entries: dict[int, Overview] = {}

    async def get(self, user_id: int) -> Overview | None:
        return self.entries.get(user_id)

    async def set(self, user_id: int, overview: Overview, ttl_seconds: int) -> None:
        self.entries[user_id] = overview

    async def invalidate(self, user_id: int) -> None:
        self.entries.pop(user_id, None)


class PlainCipher:
    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc:")
