"""In-memory fakes for the application ports, shared by unit and integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.errors import ActionsUnavailable, AuthenticationError
from app.application.ports import RepositoryPage, SessionRecord
from app.domain.models import (
    ChecksState,
    Inbox,
    Issue,
    LastCommit,
    Mergeable,
    Notification,
    Overview,
    Preferences,
    PullRequest,
    RateLimit,
    ReleaseInfo,
    Repository,
    ReviewDecision,
    RunStatus,
    SeverityCounts,
    Snapshot,
    User,
    WorkflowRun,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

DEFAULT_RATE_LIMIT = RateLimit(4990, 5000, NOW)

VIEWER = User(id=42, login="octocat", name="Octo Cat", avatar_url="https://a/x.png", html_url="h")


def make_pr(
    number: int,
    *,
    author: str | None = "someone",
    is_draft: bool = False,
    updated_at: datetime = NOW,
    created_at: datetime | None = None,
    head_branch: str | None = "feature",
    review_decision: ReviewDecision | None = None,
    checks: ChecksState | None = None,
    mergeable: Mergeable = Mergeable.MERGEABLE,
    is_bot: bool = False,
) -> PullRequest:
    return PullRequest(
        number=number,
        title=f"PR {number}",
        url=f"u/{number}",
        author=author,
        is_draft=is_draft,
        updated_at=updated_at,
        created_at=created_at if created_at is not None else updated_at,
        head_branch=head_branch,
        review_decision=review_decision,
        checks=checks,
        mergeable=mergeable,
        is_bot=is_bot,
    )


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
    bot_prs: int = 0,
    pr_updated_at: datetime = NOW,
    issue_updated_at: datetime = NOW,
    last_commit: LastCommit | None = None,
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
            make_pr(
                i,
                author="dependabot[bot]" if i <= bot_prs else "someone",
                updated_at=pr_updated_at,
                is_bot=i <= bot_prs,
            )
            for i in range(1, prs + 1)
        ),
        issues=tuple(
            Issue(i, f"Issue {i}", f"u/{i}", "someone", issue_updated_at)
            for i in range(1, issues + 1)
        ),
        last_commit=last_commit,
    )


def make_run(
    status: RunStatus,
    *,
    run_id: int = 1,
    workflow: str = "ci",
    branch: str = "main",
    age_minutes: int = 0,
    created_at: datetime | None = None,
) -> WorkflowRun:
    updated = NOW - timedelta(minutes=age_minutes)
    return WorkflowRun(
        id=run_id,
        workflow_name=workflow,
        title=f"run {run_id}",
        url=f"https://github.com/x/y/actions/runs/{run_id}",
        branch=branch,
        event="push",
        status=status,
        run_number=run_id,
        created_at=created_at if created_at is not None else updated,
        updated_at=updated,
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
        inbox: Inbox | None = None,
        dependabot_by_repo: dict[str, tuple[SeverityCounts | None, int | None]] | None = None,
        release_by_repo: dict[str, ReleaseInfo | None] | None = None,
        security: dict[str, tuple[SeverityCounts | None, int | None]] | None = None,
        commits_since: dict[str, int | None] | None = None,
        notifications: list[Notification] | None = None,
    ) -> None:
        self.repos = repos or []
        self.runs = runs or {}
        self.rate_limit = rate_limit
        self.inbox = inbox if inbox is not None else Inbox((), (), (), ())
        self.dependabot_by_repo = dependabot_by_repo or {}
        self.release_by_repo = release_by_repo or {}
        self.security = security or {}
        self.commits_since = commits_since or {}
        # None means the `notifications` scope is missing, matching the real port method.
        self.notifications = notifications
        self.unavailable: set[str] = set()
        self.calls = 0
        self.run_calls: list[str] = []
        self.inbox_calls = 0
        self.security_calls: list[str] = []
        self.commits_since_calls: list[str] = []
        self.notifications_calls = 0
        self.token_valid = True
        self.inbox_error: Exception | None = None
        self.security_error: Exception | None = None
        self.commits_since_error: Exception | None = None
        self.notifications_error: Exception | None = None

    async def list_repositories(self, token: str) -> RepositoryPage:
        self.calls += 1
        if not self.token_valid:
            raise AuthenticationError("revoked")
        return RepositoryPage(
            tuple(self.repos),
            self.rate_limit,
            dict(self.dependabot_by_repo),
            dict(self.release_by_repo),
        )

    async def list_recent_runs(
        self, token: str, owner: str, name: str, limit: int
    ) -> list[WorkflowRun]:
        full = f"{owner}/{name}"
        self.run_calls.append(full)
        if full in self.unavailable:
            raise ActionsUnavailable("disabled")
        return self.runs.get(full, [])[:limit]

    async def search_inbox(self, token: str) -> Inbox:
        self.inbox_calls += 1
        if self.inbox_error is not None:
            raise self.inbox_error
        return self.inbox

    async def fetch_security(
        self, token: str, owner: str, name: str
    ) -> tuple[SeverityCounts | None, int | None]:
        full = f"{owner}/{name}"
        self.security_calls.append(full)
        if self.security_error is not None:
            raise self.security_error
        return self.security.get(full, (None, None))

    async def count_commits_since(
        self, token: str, owner: str, name: str, base: str, head: str
    ) -> int | None:
        full = f"{owner}/{name}"
        self.commits_since_calls.append(full)
        if self.commits_since_error is not None:
            raise self.commits_since_error
        return self.commits_since.get(full)

    async def list_notifications(self, token: str) -> list[Notification] | None:
        self.notifications_calls += 1
        if self.notifications_error is not None:
            raise self.notifications_error
        return self.notifications


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


class FakeUserState:
    def __init__(self) -> None:
        self.preferences: dict[int, Preferences] = {}
        self.snapshots: dict[int, Snapshot] = {}

    async def get_preferences(self, user_id: int) -> Preferences:
        return self.preferences.get(user_id, Preferences())

    async def set_preferences(self, user_id: int, prefs: Preferences) -> None:
        self.preferences[user_id] = prefs

    async def get_snapshot(self, user_id: int) -> Snapshot | None:
        return self.snapshots.get(user_id)

    async def set_snapshot(self, user_id: int, snapshot: Snapshot) -> None:
        self.snapshots[user_id] = snapshot
