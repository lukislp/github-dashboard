import dataclasses
from datetime import timedelta

from app.domain.models import (
    AttentionItem,
    AttentionKind,
    Inbox,
    Notification,
    RepoSecurity,
    RunStatus,
    SeverityCounts,
)
from app.domain.overview import build_overview, classify_ci
from app.domain.snapshot import diff_since, snapshot_of
from tests.fakes import NOW, make_pr, make_repo, make_run

LATER = NOW + timedelta(hours=1)


def _inbox_item(number: int, kind: AttentionKind = AttentionKind.REVIEW_REQUESTED) -> AttentionItem:
    return AttentionItem(
        kind=kind,
        is_pull_request=True,
        repo_full_name="octocat/a",
        number=number,
        title=f"PR {number}",
        url=f"u/{number}",
        author="bob",
        updated_at=NOW,
        is_draft=False,
    )


def _overview_with_one_pr():
    repo = dataclasses.replace(
        make_repo("a", issues=1), pull_requests=(make_pr(1),), open_pr_count=1
    )
    return build_overview(
        viewer_login="octocat",
        repositories=[repo],
        ci_by_repo={"octocat/a": classify_ci([make_run_failure()])},
        rate_limit=None,
        now=NOW,
        inbox=Inbox(
            review_requested=(_inbox_item(1),), changes_requested=(), assigned=(), mentioned=()
        ),
        security_by_repo={"octocat/a": RepoSecurity(SeverityCounts(1, 0, 0, 0), 1, None, None)},
        notifications=(_notification("n1"),),
        notifications_available=True,
    )


def make_run_failure():
    return make_run(RunStatus.FAILURE, run_id=99)


def _notification(notification_id: str) -> Notification:
    return Notification(
        id=notification_id,
        reason="mention",
        subject_title="t",
        subject_type="Issue",
        subject_url="u",
        repo_full_name="octocat/a",
        updated_at=NOW,
        unread=True,
    )


def test_snapshot_of_captures_every_kind_of_key():
    overview = _overview_with_one_pr()
    snapshot = snapshot_of(overview, NOW)

    assert snapshot.taken_at == NOW
    assert snapshot.prs == {"octocat/a#1"}
    assert snapshot.issues == {"octocat/a#1"}
    assert snapshot.failed_runs == {99}
    assert snapshot.inbox == {"review_requested:octocat/a#1"}
    assert snapshot.notifications == {"n1"}
    assert snapshot.alert_repos == {"octocat/a"}


def test_diff_since_none_snapshot_is_empty():
    overview = _overview_with_one_pr()
    changes = diff_since(overview, None)

    assert changes.since is None
    assert changes.total == 0
    assert changes.new_prs == ()
    assert changes.new_issues == ()
    assert changes.new_failed_runs == ()
    assert changes.new_inbox == ()
    assert changes.new_notifications == ()
    assert changes.new_alert_repos == ()


def test_diff_since_reports_new_pr_and_keeps_resolved_ones_out():
    baseline = _overview_with_one_pr()
    snapshot = snapshot_of(baseline, NOW)

    repo = dataclasses.replace(
        make_repo("a"),
        pull_requests=(make_pr(1, updated_at=LATER), make_pr(2, updated_at=LATER)),
        open_pr_count=2,
    )
    later_overview = build_overview(
        viewer_login="octocat",
        repositories=[repo],
        ci_by_repo={},
        rate_limit=None,
        now=LATER,
    )

    changes = diff_since(later_overview, snapshot)

    assert changes.since == NOW
    assert [item.number for item in changes.new_prs] == [2]
    assert changes.new_prs[0].repo_full_name == "octocat/a"
    assert changes.new_prs[0].is_pull_request is True
    # The PR that was already in the snapshot (and the issue that disappeared) are not "new".
    assert changes.new_issues == ()
    assert changes.total == 1


def test_diff_since_reports_new_failed_run_new_inbox_notification_and_alert_repo():
    baseline = build_overview(
        viewer_login="octocat",
        repositories=[make_repo("a")],
        ci_by_repo={},
        rate_limit=None,
        now=NOW,
    )
    snapshot = snapshot_of(baseline, NOW)

    later = _overview_with_one_pr()
    changes = diff_since(later, snapshot)

    assert [f.run.id for f in changes.new_failed_runs] == [99]
    assert [item.number for item in changes.new_inbox] == [1]
    assert [n.id for n in changes.new_notifications] == ["n1"]
    assert changes.new_alert_repos == ("octocat/a",)
    # pr + issue + failed run + inbox item + notification + alert repo, all newly appeared.
    assert changes.total == 6
