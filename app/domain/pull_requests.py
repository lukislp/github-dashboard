"""Pull-request domain logic: derived state and bot detection. Pure functions."""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import ChecksState, Mergeable, PullRequest, ReviewDecision

_BOT_LOGINS = frozenset(
    {"dependabot", "renovate", "github-actions", "dependabot-preview", "renovate-bot"}
)


class PrState(StrEnum):
    """One glance summary of a pull request's condition, most urgent state first."""

    CONFLICT = "conflict"
    FAILING = "failing"
    CHANGES_REQUESTED = "changes_requested"
    DRAFT = "draft"
    PENDING_CHECKS = "pending_checks"
    READY = "ready"
    AWAITING_REVIEW = "awaiting_review"


def is_bot_login(login: str | None) -> bool:
    """True when the author login is missing or looks like a bot account."""
    if login is None:
        return True
    normalised = login.lower()
    return normalised.endswith("[bot]") or normalised in _BOT_LOGINS


def pr_state(pr: PullRequest) -> PrState:
    """Derive one overall state for a pull request.

    Precedence: a merge conflict trumps everything, then failing checks, then a
    reviewer requesting changes, then draft status, then checks still running.
    What remains is either approved-and-clean (ready) or awaiting a first look,
    which also covers an explicit REVIEW_REQUIRED decision.
    """
    if pr.mergeable == Mergeable.CONFLICTING:
        return PrState.CONFLICT
    if pr.checks in (ChecksState.FAILURE, ChecksState.ERROR):
        return PrState.FAILING
    if pr.review_decision == ReviewDecision.CHANGES_REQUESTED:
        return PrState.CHANGES_REQUESTED
    if pr.is_draft:
        return PrState.DRAFT
    if pr.checks == ChecksState.PENDING:
        return PrState.PENDING_CHECKS
    if (
        pr.review_decision == ReviewDecision.APPROVED
        and pr.checks in (ChecksState.SUCCESS, None)
        and pr.mergeable != Mergeable.CONFLICTING
    ):
        return PrState.READY
    return PrState.AWAITING_REVIEW
