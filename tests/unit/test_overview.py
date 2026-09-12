from datetime import timedelta

from app.domain.models import CiState, RunStatus
from app.domain.overview import build_overview, classify_ci, skipped_ci
from tests.fakes import NOW, make_repo, make_run


def test_no_runs_is_none():
    assert classify_ci([]).state == CiState.NONE


def test_any_failed_run_among_recent_marks_failing():
    runs = [
        make_run(RunStatus.SUCCESS, run_id=5),
        make_run(RunStatus.SUCCESS, run_id=4),
        make_run(RunStatus.FAILURE, run_id=3),
        make_run(RunStatus.SUCCESS, run_id=2),
        make_run(RunStatus.TIMED_OUT, run_id=1),
    ]
    ci = classify_ci(runs)
    assert ci.state == CiState.FAILING
    assert ci.failed_count == 2
    assert ci.active_count == 0


def test_active_run_without_failures_is_running():
    ci = classify_ci([make_run(RunStatus.IN_PROGRESS, run_id=2), make_run(RunStatus.SUCCESS)])
    assert ci.state == CiState.RUNNING
    assert ci.active_count == 1


def test_all_green_is_passing():
    ci = classify_ci([make_run(RunStatus.SUCCESS, run_id=i) for i in range(5)])
    assert ci.state == CiState.PASSING


def test_cancelled_newest_is_neutral():
    ci = classify_ci([make_run(RunStatus.CANCELLED, run_id=2), make_run(RunStatus.SUCCESS)])
    assert ci.state == CiState.NEUTRAL


def test_error_is_unavailable():
    ci = classify_ci([], error="disabled")
    assert ci.state == CiState.UNAVAILABLE
    assert ci.error == "disabled"


def test_build_overview_totals_sorting_and_failures():
    repos = [
        make_repo("green", prs=2, issues=1, stars=3, pushed_at=NOW),
        make_repo("red", prs=1, issues=4, private=True, pushed_at=NOW - timedelta(days=3)),
        make_repo("old", archived=True, fork=True, pushed_at=NOW - timedelta(days=30)),
    ]
    ci = {
        "octocat/green": classify_ci([make_run(RunStatus.SUCCESS)]),
        "octocat/red": classify_ci(
            [
                make_run(RunStatus.FAILURE, run_id=2),
                make_run(RunStatus.FAILURE, run_id=1, age_minutes=5),
            ]
        ),
        "octocat/old": skipped_ci("archived"),
    }
    overview = build_overview(
        viewer_login="octocat", repositories=repos, ci_by_repo=ci, rate_limit=None, now=NOW
    )

    assert [r.repository.name for r in overview.repos] == ["red", "green", "old"]
    totals = overview.totals
    assert totals.repos == 3
    assert totals.private == 1
    assert totals.archived == 1
    assert totals.forks == 1
    assert totals.stars == 3
    assert totals.open_prs == 3
    assert totals.open_issues == 5
    assert totals.failed_runs == 2
    assert totals.repos_failing == 1
    assert totals.active_runs == 0
    assert [f.run.id for f in overview.failures] == [2, 1]
    assert overview.failures[0].repo_full_name == "octocat/red"


def test_build_overview_uses_none_state_for_missing_ci():
    overview = build_overview(
        viewer_login="x", repositories=[make_repo("a")], ci_by_repo={}, rate_limit=None, now=NOW
    )
    assert overview.repos[0].ci.state == CiState.NONE
