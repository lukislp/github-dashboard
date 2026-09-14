"""Domain models. Pure data, no I/O, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.hygiene import RepoHygiene


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
    # Derived by `app.domain.overview.mark_pr`, same way `stale` is: whole days since
    # `created_at` (age_days) and since `updated_at` (idle_days).
    age_days: int = 0
    idle_days: int = 0


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    url: str
    author: str | None
    updated_at: datetime
    created_at: datetime
    stale: bool = False
    # Derived by `app.domain.overview.mark_issue`: whole days since `created_at`.
    age_days: int = 0


@dataclass(frozen=True, slots=True)
class LastCommit:
    sha: str
    headline: str
    author_login: str | None
    author_name: str | None
    committed_at: datetime
    url: str


@dataclass(frozen=True, slots=True)
class Branch:
    """A branch (other than the default one) with no pull request pointing at it."""

    name: str
    last_commit_at: datetime | None
    author: str | None
    stale: bool = False


@dataclass(frozen=True, slots=True)
class Repository:
    full_name: str
    node_id: str
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
    branch_count: int = 0
    branches_without_pr: tuple[Branch, ...] = ()


@dataclass(frozen=True, slots=True)
class FailedJob:
    """One failed job of a workflow run, with the first step that failed in it."""

    name: str
    step: str | None
    url: str


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
    # REST `run_started_at`, falling back to `created_at`. Used for `duration_seconds`.
    started_at: datetime
    long_running: bool = False
    # Populated only for failed runs, newest first and capped by `MAX_JOB_LOOKUPS` across the
    # whole refresh (see `app.application.use_cases.GetOverview`); empty otherwise, including
    # when the per-run jobs lookup degraded (404/403/GitHubUnavailable).
    failed_jobs: tuple[FailedJob, ...] = ()

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def duration_seconds(self) -> int:
        """Observed wall-clock CI time. Never negative, 0 while the run is still active."""
        if self.active:
            return 0
        return max(0, int((self.updated_at - self.started_at).total_seconds()))


@dataclass(frozen=True, slots=True)
class RepoCi:
    state: CiState
    runs: tuple[WorkflowRun, ...]
    failed_count: int
    active_count: int
    error: str | None = None
    long_running_count: int = 0
    ci_seconds_recent: int = 0


@dataclass(frozen=True, slots=True)
class SeverityCounts:
    """Open alerts of one security feature, broken down by GitHub's severity levels."""

    critical: int
    high: int
    moderate: int
    low: int

    @property
    def total(self) -> int:
        return self.critical + self.high + self.moderate + self.low


@dataclass(frozen=True, slots=True)
class RepoSecurity:
    """Security alerts of one repository. `None` means unavailable, not zero.

    Unavailable covers: the feature is disabled for the repository, the token lacks the
    required scope/permission, or GitHub answered 403/404 for that part.
    """

    dependabot: SeverityCounts | None
    dependabot_total: int | None
    code_scanning: SeverityCounts | None
    secret_scanning: int | None

    @property
    def total(self) -> int:
        """Sum of the available parts. Secret-scanning alerts count as one each."""
        parts = 0
        if self.dependabot is not None:
            parts += self.dependabot.total
        if self.code_scanning is not None:
            parts += self.code_scanning.total
        if self.secret_scanning is not None:
            parts += self.secret_scanning
        return parts

    @property
    def has_critical(self) -> bool:
        """True when any part reports a critical severity, or a secret is exposed."""
        dependabot_critical = self.dependabot.critical if self.dependabot else 0
        code_scanning_critical = self.code_scanning.critical if self.code_scanning else 0
        return dependabot_critical > 0 or code_scanning_critical > 0 or bool(self.secret_scanning)


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str
    name: str | None
    published_at: datetime | None
    url: str
    is_prerelease: bool
    unreleased_commits: int | None


@dataclass(frozen=True, slots=True)
class Notification:
    """One unread GitHub notification for the viewer."""

    id: str
    reason: str
    subject_title: str
    subject_type: str
    subject_url: str | None
    repo_full_name: str
    updated_at: datetime
    unread: bool


@dataclass(frozen=True, slots=True)
class RepoUsage:
    """Observed CI wall-clock usage of one repository for the current calendar month.

    Summed from `GitHubApi.list_run_durations` (see `Overview.usage_since` for the window).
    This is wall-clock time, not billed minutes - see the "Actions time" section in the
    README for why GitHub's own billing endpoints cannot be used here and how the two relate.
    `truncated` is True when the repository had more than 200 runs this month: `seconds`/
    `runs` then undercount the true total.
    """

    seconds: int
    runs: int
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class RepoOverview:
    repository: Repository
    ci: RepoCi
    security: RepoSecurity
    release: ReleaseInfo | None
    hygiene: RepoHygiene
    usage: RepoUsage


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
    security_critical: int
    security_high: int
    security_total: int
    repos_with_alerts: int
    secret_alerts: int
    repos_unreleased: int
    unreleased_commits: int
    notifications_unread: int
    notifications_by_reason: dict[str, int]
    hygiene_average: int
    repos_without_ci: int
    repos_without_protection: int
    repos_without_dependency_updates: int
    repos_without_license: int
    branches_without_pr: int
    stale_branches: int
    oldest_pr_days: int
    ci_seconds_recent: int
    ci_seconds_month: int
    ci_seconds_month_private: int
    ci_seconds_month_public: int
    ci_runs_month: int


@dataclass(frozen=True, slots=True)
class ActionsUsage:
    """GitHub Actions billing usage for the signed-in user (`GET /users/{login}/settings/billing/
    actions`). `available` is False when the endpoint answered 403/404 - the token's OAuth
    scopes do not include `user` - in which case the other fields are `None`, never zero."""

    available: bool
    minutes_used: int | None
    included_minutes: int | None
    paid_minutes_used: int | None


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
    notifications: tuple[Notification, ...]
    notifications_available: bool
    actions_usage: ActionsUsage
    # The first of the current calendar month, UTC - the start of the window `RepoUsage` and
    # the `ci_seconds_month*`/`ci_runs_month` totals were summed over. `None` when `CI_USAGE`
    # is disabled, in which case every repository's `usage` is zero.
    usage_since: datetime | None


@dataclass(frozen=True, slots=True)
class RepoGroup:
    """A user-defined named group of repositories, referenced by full name."""

    name: str
    repos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Preferences:
    """Per-user dashboard preferences: custom repository groups and favourites.

    Keyed by GitHub user id, not by session; they survive logout and re-login.
    """

    groups: tuple[RepoGroup, ...] = ()
    favorites: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What the viewer had already seen as of `taken_at`, kept to diff a later Overview.

    Keys: pull requests and issues as `"owner/repo#number"`, failed runs by their id,
    inbox items as `"kind:owner/repo#number"` (kind is the `AttentionKind` value, since the
    same PR can appear under more than one reason), notifications by their id, and alert
    repos by full name (repositories with `security.total > 0`).
    """

    taken_at: datetime
    prs: frozenset[str]
    issues: frozenset[str]
    failed_runs: frozenset[int]
    inbox: frozenset[str]
    notifications: frozenset[str]
    alert_repos: frozenset[str]


@dataclass(frozen=True, slots=True)
class ChangedItem:
    """One pull request or issue that is new since the viewer's last recorded Snapshot."""

    repo_full_name: str
    number: int
    title: str
    url: str
    author: str | None
    updated_at: datetime
    is_pull_request: bool


@dataclass(frozen=True, slots=True)
class Changes:
    """What is new in an Overview compared to the viewer's last Snapshot.

    Depends on the viewer's own history, not on GitHub data, so `Overview` never stores
    this itself: the web layer computes it from the current Overview and a stored Snapshot.
    """

    since: datetime | None
    new_prs: tuple[ChangedItem, ...]
    new_issues: tuple[ChangedItem, ...]
    new_failed_runs: tuple[FailedRun, ...]
    new_inbox: tuple[AttentionItem, ...]
    new_notifications: tuple[Notification, ...]
    new_alert_repos: tuple[str, ...]

    @property
    def total(self) -> int:
        return (
            len(self.new_prs)
            + len(self.new_issues)
            + len(self.new_failed_runs)
            + len(self.new_inbox)
            + len(self.new_notifications)
            + len(self.new_alert_repos)
        )
