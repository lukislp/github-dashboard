"""Aggregation logic: turns raw repositories and runs into an Overview. Pure functions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from app.domain.models import (
    CiState,
    FailedRun,
    Overview,
    RateLimit,
    RepoCi,
    RepoOverview,
    Repository,
    RunStatus,
    Totals,
    WorkflowRun,
)


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
    return RepoCi(state, run_tuple, failed, active)


def skipped_ci(reason: str) -> RepoCi:
    """CI placeholder for repositories that were intentionally not queried (e.g. archived)."""
    return RepoCi(CiState.SKIPPED, (), 0, 0, reason)


def build_overview(
    *,
    viewer_login: str,
    repositories: Iterable[Repository],
    ci_by_repo: Mapping[str, RepoCi],
    rate_limit: RateLimit | None,
    now: datetime,
) -> Overview:
    repos: list[RepoOverview] = []
    failures: list[FailedRun] = []

    for repo in repositories:
        ci = ci_by_repo.get(repo.full_name) or classify_ci(())
        repos.append(RepoOverview(repo, ci))
        failures.extend(FailedRun(repo.full_name, run) for run in ci.runs if run.failed)

    repos.sort(key=_repo_sort_key)
    failures.sort(key=lambda f: f.run.updated_at, reverse=True)

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
    )
    return Overview(
        viewer_login=viewer_login,
        generated_at=now,
        totals=totals,
        repos=tuple(repos),
        failures=tuple(failures),
        rate_limit=rate_limit,
    )


def _repo_sort_key(item: RepoOverview) -> tuple[int, float]:
    """Failing repositories first, then most recently pushed."""
    failing = 0 if item.ci.state == CiState.FAILING else 1
    pushed = item.repository.pushed_at.timestamp() if item.repository.pushed_at else 0.0
    return (failing, -pushed)
