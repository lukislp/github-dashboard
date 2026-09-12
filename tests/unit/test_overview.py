import dataclasses
from datetime import timedelta

from app.domain.models import (
    AttentionItem,
    AttentionKind,
    ChecksState,
    CiState,
    Inbox,
    Mergeable,
    ReviewDecision,
    RunStatus,
)
from app.domain.overview import build_overview, classify_ci, skipped_ci
from tests.fakes import NOW, make_pr, make_repo, make_run


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


def test_stale_flag_set_past_threshold_and_clear_within_it():
    repo = make_repo(
        "a", prs=1, issues=1, pr_updated_at=NOW - timedelta(days=20), issue_updated_at=NOW
    )
    overview = build_overview(
        viewer_login="x",
        repositories=[repo],
        ci_by_repo={},
        rate_limit=None,
        now=NOW,
        stale_after=timedelta(days=14),
    )
    updated_repo = overview.repos[0].repository
    assert updated_repo.pull_requests[0].stale is True
    assert updated_repo.issues[0].stale is False
    assert overview.totals.stale_prs == 1
    assert overview.totals.stale_issues == 0


def test_long_running_flag_only_for_active_runs_past_threshold():
    repo = make_repo("a")
    ci = {
        "octocat/a": classify_ci(
            [
                make_run(RunStatus.IN_PROGRESS, run_id=1, created_at=NOW - timedelta(minutes=45)),
                make_run(RunStatus.IN_PROGRESS, run_id=2, created_at=NOW - timedelta(minutes=5)),
                make_run(RunStatus.SUCCESS, run_id=3, created_at=NOW - timedelta(minutes=90)),
            ]
        )
    }
    overview = build_overview(
        viewer_login="x",
        repositories=[repo],
        ci_by_repo=ci,
        rate_limit=None,
        now=NOW,
        long_run_after=timedelta(minutes=30),
    )
    runs_by_id = {r.id: r for r in overview.repos[0].ci.runs}
    assert runs_by_id[1].long_running is True
    assert runs_by_id[2].long_running is False
    assert runs_by_id[3].long_running is False
    assert overview.repos[0].ci.long_running_count == 1
    assert overview.totals.long_running_runs == 1


def test_totals_human_and_bot_prs_count_fetched_and_remainder():
    # 2 fetched PRs (1 bot, 1 human) plus 3 more open PRs GitHub reports but that weren't fetched.
    repo = make_repo("a", prs=2, bot_prs=1)
    repo = dataclasses.replace(repo, open_pr_count=5)
    overview = build_overview(
        viewer_login="x", repositories=[repo], ci_by_repo={}, rate_limit=None, now=NOW
    )
    assert overview.totals.human_prs == 1 + 3
    assert overview.totals.bot_prs == 1


def test_totals_pr_state_counters():
    approved_ready = make_pr(1, review_decision=ReviewDecision.APPROVED, checks=ChecksState.SUCCESS)
    changes_requested_pr = make_pr(2, review_decision=ReviewDecision.CHANGES_REQUESTED)
    failing_pr = make_pr(3, checks=ChecksState.FAILURE)
    conflict_pr = make_pr(4, mergeable=Mergeable.CONFLICTING)

    repo = make_repo("a", prs=0)
    repo = dataclasses.replace(
        repo,
        pull_requests=(approved_ready, changes_requested_pr, failing_pr, conflict_pr),
        open_pr_count=4,
    )
    overview = build_overview(
        viewer_login="x", repositories=[repo], ci_by_repo={}, rate_limit=None, now=NOW
    )
    totals = overview.totals
    assert totals.prs_ready == 1
    assert totals.prs_changes_requested == 1
    assert totals.prs_failing == 2  # failing + conflict


def test_totals_inbox_total_reflects_inbox():
    item = AttentionItem(
        kind=AttentionKind.REVIEW_REQUESTED,
        is_pull_request=True,
        repo_full_name="octocat/a",
        number=1,
        title="t",
        url="u",
        author="bob",
        updated_at=NOW,
        is_draft=False,
    )
    inbox = Inbox(review_requested=(item,), changes_requested=(), assigned=(item,), mentioned=())
    overview = build_overview(
        viewer_login="x",
        repositories=[make_repo("a")],
        ci_by_repo={},
        rate_limit=None,
        now=NOW,
        inbox=inbox,
    )
    assert overview.inbox is inbox
    assert overview.totals.inbox_total == 2
