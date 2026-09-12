"""Domain models. Pure data, no I/O, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RunStatus(StrEnum):
    """Normalised outcome of a workflow run (GitHub status + conclusion folded into one)."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMED_OUT = "timed_out"
    STARTUP_FAILURE = "startup_failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    ACTION_REQUIRED = "action_required"
    STALE = "stale"
    IN_PROGRESS = "in_progress"
    QUEUED = "queued"
    UNKNOWN = "unknown"


FAILED_STATUSES = frozenset({RunStatus.FAILURE, RunStatus.TIMED_OUT, RunStatus.STARTUP_FAILURE})
ACTIVE_STATUSES = frozenset({RunStatus.IN_PROGRESS, RunStatus.QUEUED})


class CiState(StrEnum):
    """Aggregated CI state of one repository, derived from its most recent runs."""

    FAILING = "failing"
    RUNNING = "running"
    PASSING = "passing"
    NEUTRAL = "neutral"
    NONE = "none"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class ReviewDecision(StrEnum):
    """GitHub's aggregated review verdict for a pull request."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REVIEW_REQUIRED = "review_required"


class ChecksState(StrEnum):
    """Rollup of a pull request's status checks on its latest commit."""

    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    ERROR = "error"


class Mergeable(StrEnum):
    """Whether a pull request can be merged without conflicts."""

    MERGEABLE = "mergeable"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class AttentionKind(StrEnum):
    """Why a pull request or issue showed up in the viewer's inbox."""

    REVIEW_REQUESTED = "review_requested"
    CHANGES_REQUESTED = "changes_requested"
    ASSIGNED = "assigned"
    MENTIONED = "mentioned"


@dataclass(frozen=True, slots=True)
class User:
    id: int
    login: str
    name: str | None
    avatar_url: str
    html_url: str


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    title: str
    url: str
    author: str | None
    is_draft: bool
    updated_at: datetime
    created_at: datetime
    head_branch: str | None
    review_decision: ReviewDecision | None
    checks: ChecksState | None
    mergeable: Mergeable
    is_bot: bool
    stale: bool = False


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    url: str
    author: str | None
    updated_at: datetime
    stale: bool = False


@dataclass(frozen=True, slots=True)
class LastCommit:
    sha: str
    headline: str
    author_login: str | None
    author_name: str | None
    committed_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class Repository:
    full_name: str
    name: str
    owner: str
    url: str
    description: str | None
    is_private: bool
    is_archived: bool
    is_fork: bool
    has_issues: bool
    stars: int
    pushed_at: datetime | None
    language: str | None
    language_color: str | None
    default_branch: str | None
    open_pr_count: int
    open_issue_count: int
    pull_requests: tuple[PullRequest, ...]
    issues: tuple[Issue, ...]
    last_commit: LastCommit | None


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: int
    workflow_name: str
    title: str
    url: str
    branch: str | None
    event: str
    status: RunStatus
    run_number: int
    created_at: datetime
    updated_at: datetime
    long_running: bool = False

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


@dataclass(frozen=True, slots=True)
class RepoCi:
    state: CiState
    runs: tuple[WorkflowRun, ...]
    failed_count: int
    active_count: int
    error: str | None = None
    long_running_count: int = 0


@dataclass(frozen=True, slots=True)
class RepoOverview:
    repository: Repository
    ci: RepoCi


@dataclass(frozen=True, slots=True)
class FailedRun:
    repo_full_name: str
    run: WorkflowRun


@dataclass(frozen=True, slots=True)
class AttentionItem:
    """One pull request or issue that is waiting on the viewer."""

    kind: AttentionKind
    is_pull_request: bool
    repo_full_name: str
    number: int
    title: str
    url: str
    author: str | None
    updated_at: datetime
    is_draft: bool


@dataclass(frozen=True, slots=True)
class Inbox:
    """Everything currently waiting on the viewer, grouped by why it needs them."""

    review_requested: tuple[AttentionItem, ...]
    changes_requested: tuple[AttentionItem, ...]
    assigned: tuple[AttentionItem, ...]
    mentioned: tuple[AttentionItem, ...]

    @property
    def total(self) -> int:
        return (
            len(self.review_requested)
            + len(self.changes_requested)
            + len(self.assigned)
            + len(self.mentioned)
        )


@dataclass(frozen=True, slots=True)
class Totals:
    repos: int
    private: int
    archived: int
    forks: int
    stars: int
    open_prs: int
    open_issues: int
    failed_runs: int
    repos_failing: int
    active_runs: int
    human_prs: int
    bot_prs: int
    stale_prs: int
    stale_issues: int
    prs_ready: int
    prs_changes_requested: int
    prs_failing: int
    long_running_runs: int
    inbox_total: int


@dataclass(frozen=True, slots=True)
class RateLimit:
    remaining: int
    limit: int
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class Overview:
    viewer_login: str
    generated_at: datetime
    totals: Totals
    repos: tuple[RepoOverview, ...]
    failures: tuple[FailedRun, ...]
    rate_limit: RateLimit | None
    inbox: Inbox
