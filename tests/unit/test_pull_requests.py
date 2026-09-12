from app.domain.models import ChecksState, Mergeable, ReviewDecision
from app.domain.pull_requests import PrState, is_bot_login, pr_state
from tests.fakes import make_pr


def test_conflict_beats_everything():
    pr = make_pr(
        1,
        mergeable=Mergeable.CONFLICTING,
        checks=ChecksState.FAILURE,
        review_decision=ReviewDecision.CHANGES_REQUESTED,
        is_draft=True,
    )
    assert pr_state(pr) == PrState.CONFLICT


def test_failing_checks_beat_changes_requested_and_draft():
    pr = make_pr(
        1,
        checks=ChecksState.FAILURE,
        review_decision=ReviewDecision.CHANGES_REQUESTED,
        is_draft=True,
    )
    assert pr_state(pr) == PrState.FAILING


def test_error_checks_are_also_failing():
    pr = make_pr(1, checks=ChecksState.ERROR)
    assert pr_state(pr) == PrState.FAILING


def test_changes_requested_beats_draft_and_pending_checks():
    pr = make_pr(
        1,
        review_decision=ReviewDecision.CHANGES_REQUESTED,
        is_draft=True,
        checks=ChecksState.PENDING,
    )
    assert pr_state(pr) == PrState.CHANGES_REQUESTED


def test_draft_beats_pending_checks():
    pr = make_pr(1, is_draft=True, checks=ChecksState.PENDING)
    assert pr_state(pr) == PrState.DRAFT


def test_pending_checks_when_not_draft_and_not_reviewed():
    pr = make_pr(1, checks=ChecksState.PENDING)
    assert pr_state(pr) == PrState.PENDING_CHECKS


def test_ready_when_approved_with_passing_checks():
    pr = make_pr(1, review_decision=ReviewDecision.APPROVED, checks=ChecksState.SUCCESS)
    assert pr_state(pr) == PrState.READY


def test_ready_when_approved_with_no_checks_configured():
    pr = make_pr(1, review_decision=ReviewDecision.APPROVED, checks=None)
    assert pr_state(pr) == PrState.READY


def test_approved_but_conflicting_is_conflict_not_ready():
    pr = make_pr(1, review_decision=ReviewDecision.APPROVED, mergeable=Mergeable.CONFLICTING)
    assert pr_state(pr) == PrState.CONFLICT


def test_review_required_is_awaiting_review():
    pr = make_pr(1, review_decision=ReviewDecision.REVIEW_REQUIRED)
    assert pr_state(pr) == PrState.AWAITING_REVIEW


def test_no_review_decision_is_awaiting_review():
    pr = make_pr(1, review_decision=None, checks=ChecksState.SUCCESS)
    assert pr_state(pr) == PrState.AWAITING_REVIEW


def test_is_bot_login_none_is_bot():
    assert is_bot_login(None) is True


def test_is_bot_login_bracket_suffix():
    assert is_bot_login("dependabot[bot]") is True
    assert is_bot_login("MyThing[Bot]") is True


def test_is_bot_login_known_accounts_case_insensitive():
    for login in ("dependabot", "Renovate", "GITHUB-ACTIONS", "dependabot-preview", "renovate-bot"):
        assert is_bot_login(login) is True


def test_is_bot_login_human():
    assert is_bot_login("octocat") is False
