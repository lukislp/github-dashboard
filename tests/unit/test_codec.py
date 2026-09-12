import dataclasses
import json

from app.domain.codec import overview_from_dict, overview_to_dict
from app.domain.models import (
    AttentionItem,
    AttentionKind,
    ChecksState,
    Inbox,
    LastCommit,
    Mergeable,
    RateLimit,
    ReviewDecision,
    RunStatus,
)
from app.domain.overview import build_overview, classify_ci
from app.domain.pull_requests import PrState
from tests.fakes import NOW, make_pr, make_repo, make_run


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
    repo_a = make_repo("a", prs=0, issues=2, last_commit=last_commit)
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
    original = build_overview(
        viewer_login="octocat",
        repositories=repos,
        ci_by_repo=ci,
        rate_limit=RateLimit(10, 5000, NOW),
        now=NOW,
        inbox=inbox,
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
