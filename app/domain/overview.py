"""Aggregation logic: turns raw repositories and runs into an Overview. Pure functions."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

from app.domain.hygiene import RepoHygiene
from app.domain.models import (
    ActionsUsage,
    CiState,
    FailedRun,
    Inbox,
    Issue,
    Notification,
    Overview,
    PullRequest,
    RateLimit,
    ReleaseInfo,
    RepoCi,
    RepoOverview,
    RepoSecurity,
    Repository,
    RunStatus,
    Totals,
    WorkflowRun,
)
from app.domain.pull_requests import PrState, pr_state

_EMPTY_INBOX = Inbox((), (), (), ())
_EMPTY_SECURITY = RepoSecurity(None, None, None, None)
_EMPTY_HYGIENE = RepoHygiene((), applicable=False)
_EMPTY_ACTIONS_USAGE = ActionsUsage(
    available=False, minutes_used=None, included_minutes=None, paid_minutes_used=None
)
DEFAULT_STALE_AFTER = timedelta(days=14)
DEFAULT_LONG_RUN_AFTER = timedelta(minutes=30)


def classify_ci(runs: Iterable[WorkflowRun], *, error: str | None = None) -> RepoCi:
    """Derive a repository's CI state from its most recent runs.

    Rule (as agreed): a repository is *failing* when any of the inspected recent runs
    failed. Otherwise it is *running* when a run is still active, *passing* when the
    newest completed run succeeded, *neutral* for cancelled/skipped outcomes.
    """
    run_tuple = tuple(runs)
    if error is not None:
        return RepoCi(CiState.UNAVAILABLE, run_tuple, 0, 0, error)
    if not run_tuple:
        return RepoCi(CiState.NONE, run_tuple, 0, 0)

    failed = sum(1 for r in run_tuple if r.failed)
    active = sum(1 for r in run_tuple if r.active)
    ci_seconds_recent = sum(r.duration_seconds for r in run_tuple)

    if failed:
        state = CiState.FAILING
    elif active:
        state = CiState.RUNNING
    else:
        newest_completed = next((r for r in run_tuple if not r.active), None)
        if newest_completed is None:
            state = CiState.RUNNING
        elif newest_completed.status == RunStatus.SUCCESS:
            state = CiState.PASSING
        else:
            state = CiState.NEUTRAL
    return RepoCi(state, run_tuple, failed, active, ci_seconds_recent=ci_seconds_recent)


def skipped_ci(reason: str) -> RepoCi:
    """CI placeholder for repositories that were intentionally not queried (e.g. archived)."""
    return RepoCi(CiState.SKIPPED, (), 0, 0, reason)


def mark_pr(pr: PullRequest, *, now: datetime, stale_after: timedelta) -> PullRequest:
    """Attach the derived 'stale' flag and age/idle day counts to one pull request.

    Reused by `build_overview` (via `_mark_stale`) and by `ListRepoItems`, so the full
    pull-request lists fetched on demand apply exactly the same rule as the overview.
    """
    return dataclasses.replace(
        pr,
        stale=(now - pr.updated_at) >= stale_after,
        age_days=(now - pr.created_at).days,
        idle_days=(now - pr.updated_at).days,
    )


def mark_issue(issue: Issue, *, now: datetime, stale_after: timedelta) -> Issue:
    """Attach the derived 'stale' flag and age day count to one issue. See `mark_pr`."""
    return dataclasses.replace(
        issue,
        stale=(now - issue.updated_at) >= stale_after,
        age_days=(now - issue.created_at).days,
    )


def _mark_stale(repo: Repository, *, now: datetime, stale_after: timedelta) -> Repository:
    """Attach the derived 'stale' flag to a repository's pull requests, issues and branches."""
    prs = tuple(mark_pr(p, now=now, stale_after=stale_after) for p in repo.pull_requests)
    issues = tuple(mark_issue(i, now=now, stale_after=stale_after) for i in repo.issues)
    branches = tuple(
        dataclasses.replace(
            b, stale=b.last_commit_at is not None and (now - b.last_commit_at) >= stale_after
        )
        for b in repo.branches_without_pr
    )
    return dataclasses.replace(repo, pull_requests=prs, issues=issues, branches_without_pr=branches)


def _mark_long_running(ci: RepoCi, *, now: datetime, long_run_after: timedelta) -> RepoCi:
    """Attach the derived 'long_running' flag to active runs and recompute the count."""
    runs = tuple(
        dataclasses.replace(r, long_running=r.active and (now - r.created_at) >= long_run_after)
        for r in ci.runs
    )
    long_running_count = sum(1 for r in runs if r.long_running)
    return dataclasses.replace(ci, runs=runs, long_running_count=long_running_count)


def build_overview(
    *,
    viewer_login: str,
    repositories: Iterable[Repository],
    ci_by_repo: Mapping[str, RepoCi],
    rate_limit: RateLimit | None,
    now: datetime,
    inbox: Inbox = _EMPTY_INBOX,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    long_run_after: timedelta = DEFAULT_LONG_RUN_AFTER,
    security_by_repo: Mapping[str, RepoSecurity] | None = None,
    release_by_repo: Mapping[str, ReleaseInfo | None] | None = None,
    hygiene_by_repo: Mapping[str, RepoHygiene] | None = None,
    notifications: tuple[Notification, ...] = (),
    notifications_available: bool = False,
    actions_usage: ActionsUsage = _EMPTY_ACTIONS_USAGE,
) -> Overview:
    repos: list[RepoOverview] = []
    failures: list[FailedRun] = []
    security_by_repo = security_by_repo or {}
    release_by_repo = release_by_repo or {}
    hygiene_by_repo = hygiene_by_repo or {}

    for repo in repositories:
        repo = _mark_stale(repo, now=now, stale_after=stale_after)
        ci = ci_by_repo.get(repo.full_name) or classify_ci(())
        ci = _mark_long_running(ci, now=now, long_run_after=long_run_after)
        security = security_by_repo.get(repo.full_name) or _EMPTY_SECURITY
        release = release_by_repo.get(repo.full_name)
        hygiene = hygiene_by_repo.get(repo.full_name) or _EMPTY_HYGIENE
        repos.append(RepoOverview(repo, ci, security, release, hygiene))
        failures.extend(FailedRun(repo.full_name, run) for run in ci.runs if run.failed)

    repos.sort(key=_repo_sort_key)
    failures.sort(key=lambda f: f.run.updated_at, reverse=True)

    all_prs = [p for r in repos for p in r.repository.pull_requests]
    all_issues = [i for r in repos for i in r.repository.issues]

    human_prs = 0
    bot_prs = 0
    for r in repos:
        prs = r.repository.pull_requests
        human_prs += sum(1 for p in prs if not p.is_bot)
        bot_prs += sum(1 for p in prs if p.is_bot)
        human_prs += max(0, r.repository.open_pr_count - len(prs))

    totals = Totals(
        repos=len(repos),
        private=sum(1 for r in repos if r.repository.is_private),
        archived=sum(1 for r in repos if r.repository.is_archived),
        forks=sum(1 for r in repos if r.repository.is_fork),
        stars=sum(r.repository.stars for r in repos),
        open_prs=sum(r.repository.open_pr_count for r in repos),
        open_issues=sum(r.repository.open_issue_count for r in repos),
        failed_runs=sum(r.ci.failed_count for r in repos),
        repos_failing=sum(1 for r in repos if r.ci.state == CiState.FAILING),
        active_runs=sum(r.ci.active_count for r in repos),
        human_prs=human_prs,
        bot_prs=bot_prs,
        stale_prs=sum(1 for p in all_prs if p.stale),
        stale_issues=sum(1 for i in all_issues if i.stale),
        prs_ready=sum(1 for p in all_prs if pr_state(p) == PrState.READY),
        prs_changes_requested=sum(1 for p in all_prs if pr_state(p) == PrState.CHANGES_REQUESTED),
        prs_failing=sum(1 for p in all_prs if pr_state(p) in (PrState.FAILING, PrState.CONFLICT)),
        long_running_runs=sum(r.ci.long_running_count for r in repos),
        inbox_total=inbox.total,
        security_critical=sum(
            (r.security.dependabot.critical if r.security.dependabot else 0)
            + (r.security.code_scanning.critical if r.security.code_scanning else 0)
            for r in repos
        ),
        security_high=sum(
            (r.security.dependabot.high if r.security.dependabot else 0)
            + (r.security.code_scanning.high if r.security.code_scanning else 0)
            for r in repos
        ),
        security_total=sum(r.security.total for r in repos),
        repos_with_alerts=sum(1 for r in repos if r.security.total > 0),
        secret_alerts=sum(r.security.secret_scanning or 0 for r in repos),
        repos_unreleased=sum(
            1 for r in repos if r.release and (r.release.unreleased_commits or 0) > 0
        ),
        unreleased_commits=sum(r.release.unreleased_commits or 0 for r in repos if r.release),
        notifications_unread=sum(1 for n in notifications if n.unread),
        notifications_by_reason=_group_by_reason(notifications),
        hygiene_average=_hygiene_average(repos),
        repos_without_ci=_repos_missing_check(repos, "ci_workflow"),
        repos_without_protection=_repos_missing_check(repos, "branch_protection"),
        repos_without_dependency_updates=_repos_missing_check(repos, "dependency_updates"),
        repos_without_license=_repos_missing_check(repos, "license"),
        branches_without_pr=sum(len(r.repository.branches_without_pr) for r in repos),
        stale_branches=sum(1 for r in repos for b in r.repository.branches_without_pr if b.stale),
        oldest_pr_days=max((p.age_days for p in all_prs), default=0),
        ci_seconds_recent=sum(r.ci.ci_seconds_recent for r in repos),
    )
    return Overview(
        viewer_login=viewer_login,
        generated_at=now,
        totals=totals,
        repos=tuple(repos),
        failures=tuple(failures),
        rate_limit=rate_limit,
        inbox=inbox,
        notifications=notifications,
        notifications_available=notifications_available,
        actions_usage=actions_usage,
    )


def _hygiene_average(repos: list[RepoOverview]) -> int:
    """Mean hygiene score over repositories the checks apply to. 100 when there are none."""
    scores = [r.hygiene.score for r in repos if r.hygiene.applicable]
    if not scores:
        return 100
    return round(sum(scores) / len(scores))


def _repos_missing_check(repos: list[RepoOverview], key: str) -> int:
    """Count repositories (the checks apply to) that fail the given hygiene check."""
    return sum(1 for r in repos if r.hygiene.applicable and key in r.hygiene.failing_keys)


def _group_by_reason(notifications: Iterable[Notification]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for notification in notifications:
        counts[notification.reason] = counts.get(notification.reason, 0) + 1
    return counts


def _repo_sort_key(item: RepoOverview) -> tuple[int, float]:
    """Failing repositories first, then most recently pushed."""
    failing = 0 if item.ci.state == CiState.FAILING else 1
    pushed = item.repository.pushed_at.timestamp() if item.repository.pushed_at else 0.0
    return (failing, -pushed)
