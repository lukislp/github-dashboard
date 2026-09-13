import dataclasses
import json

from app.domain.codec import (
    changes_from_dict,
    changes_to_dict,
    overview_from_dict,
    overview_to_dict,
    preferences_from_dict,
    preferences_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from app.domain.hygiene import HygieneCheck, RepoHygiene
from app.domain.models import (
    AttentionItem,
    AttentionKind,
    ChangedItem,
    Changes,
    ChecksState,
    FailedRun,
    Inbox,
    LastCommit,
    Mergeable,
    Notification,
    Preferences,
    RateLimit,
    ReleaseInfo,
    RepoGroup,
    RepoSecurity,
    ReviewDecision,
    RunStatus,
    SeverityCounts,
    Snapshot,
)
from app.domain.overview import build_overview, classify_ci
from app.domain.pull_requests import PrState
from tests.fakes import NOW, make_branch, make_hygiene, make_pr, make_repo, make_run


def test_overview_roundtrip_through_json():
    pr = make_pr(
        1,
        review_decision=ReviewDecision.CHANGES_REQUESTED,
        checks=ChecksState.FAILURE,
        mergeable=Mergeable.CONFLICTING,
        is_bot=True,
    )
    last_commit = LastCommit(
        sha="abc123",
        headline="fix: thing",
        author_login="octocat",
        author_name="Octo Cat",
        committed_at=NOW,
        url="https://github.com/octocat/a/commit/abc123",
    )
    branch = make_branch("feature-old", last_commit_at=NOW, author="ada")
    repo_a = make_repo("a", prs=0, issues=2, last_commit=last_commit, branches=(branch,))
    repo_a = dataclasses.replace(repo_a, pull_requests=(pr,), open_pr_count=1)
    repos = [repo_a, make_repo("b", archived=True)]
    ci = {
        "octocat/a": classify_ci(
            [make_run(RunStatus.FAILURE), make_run(RunStatus.QUEUED, run_id=2)]
        )
    }
    inbox = Inbox(
        review_requested=(
            AttentionItem(
                kind=AttentionKind.REVIEW_REQUESTED,
                is_pull_request=True,
                repo_full_name="octocat/a",
                number=1,
                title="t",
                url="u",
                author="bob",
                updated_at=NOW,
                is_draft=False,
            ),
        ),
        changes_requested=(),
        assigned=(),
        mentioned=(),
    )
    security_by_repo = {
        "octocat/a": RepoSecurity(
            dependabot=SeverityCounts(1, 0, 0, 0),
            dependabot_total=1,
            code_scanning=SeverityCounts(0, 1, 0, 0),
            secret_scanning=1,
        ),
        "octocat/b": RepoSecurity(None, None, None, None),
    }
    release_by_repo = {
        "octocat/a": ReleaseInfo(
            tag="v1.0.0",
            name="First release",
            published_at=NOW,
            url="https://github.com/octocat/a/releases/tag/v1.0.0",
            is_prerelease=False,
            unreleased_commits=4,
        ),
        "octocat/b": None,
    }
    notifications = (
        Notification(
            id="1",
            reason="review_requested",
            subject_title="PR 1",
            subject_type="PullRequest",
            subject_url="https://github.com/octocat/a/pull/1",
            repo_full_name="octocat/a",
            updated_at=NOW,
            unread=True,
        ),
    )
    hygiene_by_repo = {
        "octocat/a": make_hygiene(failing=("codeowners", "security_policy")),
        "octocat/b": make_hygiene(applicable=False),
    }
    original = build_overview(
        viewer_login="octocat",
        repositories=repos,
        ci_by_repo=ci,
        rate_limit=RateLimit(10, 5000, NOW),
        now=NOW,
        inbox=inbox,
        security_by_repo=security_by_repo,
        release_by_repo=release_by_repo,
        hygiene_by_repo=hygiene_by_repo,
        notifications=notifications,
        notifications_available=True,
    )

    restored = overview_from_dict(json.loads(json.dumps(overview_to_dict(original))))

    assert restored == original


def test_run_dict_exposes_derived_flags():
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={"octocat/a": classify_ci([make_run(RunStatus.TIMED_OUT)])},
            rate_limit=None,
            now=NOW,
        )
    )
    run = payload["repos"][0]["ci"]["runs"][0]
    assert run["failed"] is True
    assert run["active"] is False
    assert run["long_running"] is False
    assert payload["rate_limit"] is None


def test_pr_dict_exposes_derived_state_and_flags():
    pr = make_pr(
        1,
        review_decision=ReviewDecision.APPROVED,
        checks=ChecksState.SUCCESS,
        is_bot=False,
        updated_at=NOW,
    )
    repo = dataclasses.replace(make_repo("a"), pull_requests=(pr,), open_pr_count=1)
    payload = overview_to_dict(
        build_overview(
            viewer_login="o", repositories=[repo], ci_by_repo={}, rate_limit=None, now=NOW
        )
    )
    pr_dict = payload["repos"][0]["repository"]["pull_requests"][0]
    assert pr_dict["state"] == PrState.READY.value
    assert pr_dict["is_bot"] is False
    assert pr_dict["stale"] is False
    assert pr_dict["mergeable"] == Mergeable.MERGEABLE.value


def test_inbox_dict_round_trips_and_totals():
    item = AttentionItem(
        kind=AttentionKind.ASSIGNED,
        is_pull_request=False,
        repo_full_name="octocat/a",
        number=3,
        title="t",
        url="u",
        author=None,
        updated_at=NOW,
        is_draft=False,
    )
    inbox = Inbox(review_requested=(), changes_requested=(), assigned=(item,), mentioned=())
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={},
            rate_limit=None,
            now=NOW,
            inbox=inbox,
        )
    )
    assert payload["inbox"]["total"] == 1
    assert payload["inbox"]["assigned"][0]["number"] == 3
    assert payload["totals"]["inbox_total"] == 1


def test_security_dict_exposes_derived_total_and_has_critical():
    security_by_repo = {
        "octocat/a": RepoSecurity(
            dependabot=SeverityCounts(1, 0, 0, 0),
            dependabot_total=1,
            code_scanning=None,
            secret_scanning=None,
        )
    }
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={},
            rate_limit=None,
            now=NOW,
            security_by_repo=security_by_repo,
        )
    )
    security = payload["repos"][0]["security"]
    assert security["dependabot"]["critical"] == 1
    assert security["dependabot"]["total"] == 1
    assert security["code_scanning"] is None
    assert security["total"] == 1
    assert security["has_critical"] is True


def test_repo_security_missing_from_payload_defaults_to_fully_unavailable():
    repo = overview_from_dict(
        {
            "viewer_login": "o",
            "generated_at": NOW.isoformat(),
            "totals": dataclasses.asdict(
                build_overview(
                    viewer_login="o",
                    repositories=[make_repo("a")],
                    ci_by_repo={},
                    rate_limit=None,
                    now=NOW,
                ).totals
            ),
            "repos": [
                {
                    "repository": {
                        "full_name": "octocat/a",
                        "name": "a",
                        "owner": "octocat",
                        "url": "u",
                        "description": None,
                        "is_private": False,
                        "is_archived": False,
                        "is_fork": False,
                        "has_issues": True,
                        "stars": 0,
                        "pushed_at": None,
                        "language": None,
                        "language_color": None,
                        "default_branch": None,
                        "open_pr_count": 0,
                        "open_issue_count": 0,
                    },
                    "ci": {
                        "state": "none",
                        "runs": [],
                        "failed_count": 0,
                        "active_count": 0,
                    },
                }
            ],
            "failures": [],
        }
    ).repos[0]
    assert repo.security == RepoSecurity(None, None, None, None)
    assert repo.release is None


def test_hygiene_dict_exposes_score_and_round_trips():
    hygiene_by_repo = {"octocat/a": make_hygiene(failing=("readme", "license"))}
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={},
            rate_limit=None,
            now=NOW,
            hygiene_by_repo=hygiene_by_repo,
        )
    )
    hygiene = payload["repos"][0]["hygiene"]
    assert hygiene["total"] == 9
    assert hygiene["passed"] == 7
    assert hygiene["applicable"] is True
    assert hygiene["score"] == 78  # round(100 * 7 / 9)
    assert {c["key"] for c in hygiene["checks"] if not c["ok"]} == {"readme", "license"}

    restored = overview_from_dict(json.loads(json.dumps(payload)))
    assert restored.repos[0].hygiene == RepoHygiene(
        checks=tuple(HygieneCheck(c["key"], c["ok"], c["detail"]) for c in hygiene["checks"]),
        applicable=True,
    )


def test_hygiene_missing_from_payload_defaults_to_not_applicable():
    repo = overview_from_dict(
        {
            "viewer_login": "o",
            "generated_at": NOW.isoformat(),
            "totals": dataclasses.asdict(
                build_overview(
                    viewer_login="o",
                    repositories=[make_repo("a")],
                    ci_by_repo={},
                    rate_limit=None,
                    now=NOW,
                ).totals
            ),
            "repos": [
                {
                    "repository": {
                        "full_name": "octocat/a",
                        "name": "a",
                        "owner": "octocat",
                        "url": "u",
                        "description": None,
                        "is_private": False,
                        "is_archived": False,
                        "is_fork": False,
                        "has_issues": True,
                        "stars": 0,
                        "pushed_at": None,
                        "language": None,
                        "language_color": None,
                        "default_branch": None,
                        "open_pr_count": 0,
                        "open_issue_count": 0,
                    },
                    "ci": {
                        "state": "none",
                        "runs": [],
                        "failed_count": 0,
                        "active_count": 0,
                    },
                }
            ],
            "failures": [],
        }
    ).repos[0]
    assert repo.hygiene == RepoHygiene((), applicable=False)
    assert repo.repository.branch_count == 0
    assert repo.repository.branches_without_pr == ()


def test_branch_dict_round_trips():
    branch = make_branch("feature-x", last_commit_at=NOW, author="ada")
    repo = make_repo("a", branches=(branch,), branch_count=3)
    payload = overview_to_dict(
        build_overview(
            viewer_login="o", repositories=[repo], ci_by_repo={}, rate_limit=None, now=NOW
        )
    )
    repo_dict = payload["repos"][0]["repository"]
    assert repo_dict["branch_count"] == 3
    assert repo_dict["branches_without_pr"] == [
        {"name": "feature-x", "last_commit_at": NOW.isoformat(), "author": "ada", "stale": False}
    ]
    restored = overview_from_dict(json.loads(json.dumps(payload)))
    assert restored.repos[0].repository.branches_without_pr == (branch,)
    assert restored.repos[0].repository.branch_count == 3


def test_notification_dict_round_trips():
    notification = Notification(
        id="42",
        reason="mention",
        subject_title="Bug",
        subject_type="Issue",
        subject_url="https://github.com/octocat/a/issues/1",
        repo_full_name="octocat/a",
        updated_at=NOW,
        unread=True,
    )
    payload = overview_to_dict(
        build_overview(
            viewer_login="o",
            repositories=[make_repo("a")],
            ci_by_repo={},
            rate_limit=None,
            now=NOW,
            notifications=(notification,),
            notifications_available=True,
        )
    )
    assert payload["notifications_available"] is True
    assert payload["notifications"][0]["id"] == "42"
    assert payload["notifications"][0]["reason"] == "mention"
    restored = overview_from_dict(json.loads(json.dumps(payload)))
    assert restored.notifications == (notification,)


def test_preferences_roundtrip_through_json():
    prefs = Preferences(
        groups=(
            RepoGroup("Backend", ("octocat/a", "octocat/b")),
            RepoGroup("Frontend", ()),
        ),
        favorites=("octocat/a", "octocat/c"),
    )

    restored = preferences_from_dict(json.loads(json.dumps(preferences_to_dict(prefs))))

    assert restored == prefs


def test_preferences_from_dict_defaults_missing_fields():
    assert preferences_from_dict({}) == Preferences()


def test_snapshot_roundtrip_through_json():
    snapshot = Snapshot(
        taken_at=NOW,
        prs=frozenset({"octocat/a#1", "octocat/a#2"}),
        issues=frozenset({"octocat/a#3"}),
        failed_runs=frozenset({1, 2}),
        inbox=frozenset({"review_requested:octocat/a#1"}),
        notifications=frozenset({"n1"}),
        alert_repos=frozenset({"octocat/a"}),
    )

    restored = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(snapshot))))

    assert restored == snapshot


def test_changes_roundtrip_through_json():
    changed_pr = ChangedItem(
        repo_full_name="octocat/a",
        number=1,
        title="t",
        url="u",
        author="bob",
        updated_at=NOW,
        is_pull_request=True,
    )
    changed_issue = dataclasses.replace(changed_pr, number=2, is_pull_request=False)
    failed_run = FailedRun("octocat/a", make_run(RunStatus.FAILURE, run_id=9))
    inbox_item = AttentionItem(
        kind=AttentionKind.MENTIONED,
        is_pull_request=True,
        repo_full_name="octocat/a",
        number=1,
        title="t",
        url="u",
        author="bob",
        updated_at=NOW,
        is_draft=False,
    )
    notification = Notification(
        id="n1",
        reason="mention",
        subject_title="t",
        subject_type="Issue",
        subject_url="u",
        repo_full_name="octocat/a",
        updated_at=NOW,
        unread=True,
    )
    changes = Changes(
        since=NOW,
        new_prs=(changed_pr,),
        new_issues=(changed_issue,),
        new_failed_runs=(failed_run,),
        new_inbox=(inbox_item,),
        new_notifications=(notification,),
        new_alert_repos=("octocat/a",),
    )

    payload = changes_to_dict(changes)
    assert payload["total"] == 6
    restored = changes_from_dict(json.loads(json.dumps(payload)))

    assert restored == changes


def test_changes_from_dict_with_no_since_defaults_to_empty():
    restored = changes_from_dict({"since": None})
    assert restored == Changes(
        since=None,
        new_prs=(),
        new_issues=(),
        new_failed_runs=(),
        new_inbox=(),
        new_notifications=(),
        new_alert_repos=(),
    )
