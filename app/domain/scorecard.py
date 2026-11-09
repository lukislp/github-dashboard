"""OpenSSF Scorecard result of one public repository: pure data, banding, ordering, JSON shape."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import quote

# Band boundaries of the chip and the per-check bars: at least GOOD_FROM is "good", at least
# OK_FROM is "warning" (the theme token of the middle band), anything below is "critical".
GOOD_FROM = 8.0
OK_FROM = 6.0

NOT_APPLICABLE = -1


def band(score: float) -> str:
    """Theme tone (`good` / `warning` / `critical`) of a 0-10 score."""
    if score >= GOOD_FROM:
        return "good"
    if score >= OK_FROM:
        return "warning"
    return "critical"


class ScorecardState(StrEnum):
    SCORED = "scored"
    # The repository has never been scored (Scorecard answered 404).
    NONE = "none"
    # Scorecard could not be asked or answered something unusable right now.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ScorecardCheck:
    name: str
    # 0-10, or NOT_APPLICABLE (-1) when the check does not apply to the repository.
    score: int
    reason: str
    details: tuple[str, ...]
    doc_url: str | None
    doc_short: str | None


@dataclass(frozen=True, slots=True)
class Scorecard:
    score: float
    scanned_at: str | None
    version: str | None
    checks: tuple[ScorecardCheck, ...]


@dataclass(frozen=True, slots=True)
class ScorecardLookup:
    state: ScorecardState
    scorecard: Scorecard | None = None


UNKNOWN = ScorecardLookup(ScorecardState.UNKNOWN)
NOT_SCORED = ScorecardLookup(ScorecardState.NONE)


def weakest_first(checks: tuple[ScorecardCheck, ...]) -> tuple[ScorecardCheck, ...]:
    """Lowest score first; checks that do not apply go last; ties by name."""
    return tuple(
        sorted(checks, key=lambda c: (c.score == NOT_APPLICABLE, c.score, c.name.casefold()))
    )


def viewer_url(owner: str, name: str) -> str:
    return f"https://scorecard.dev/viewer/?uri=github.com/{quote(owner)}/{quote(name)}"


def summary_to_dict(lookup: ScorecardLookup) -> dict[str, Any]:
    """The compact form the repository list needs: state, and for a scored repository the
    overall score and its band."""
    body: dict[str, Any] = {"state": lookup.state.value}
    if lookup.scorecard is not None:
        body["score"] = lookup.scorecard.score
        body["band"] = band(lookup.scorecard.score)
    return body


def detail_to_dict(owner: str, name: str, lookup: ScorecardLookup) -> dict[str, Any]:
    """The full breakdown, checks ordered weakest first."""
    body = summary_to_dict(lookup)
    body["viewer_url"] = viewer_url(owner, name)
    card = lookup.scorecard
    if card is not None:
        body["scanned_at"] = card.scanned_at
        body["version"] = card.version
        body["checks"] = [
            {
                "name": c.name,
                "score": c.score,
                "band": band(c.score) if c.score != NOT_APPLICABLE else None,
                "reason": c.reason,
                "details": list(c.details),
                "doc_url": c.doc_url,
                "doc_short": c.doc_short,
            }
            for c in weakest_first(card.checks)
        ]
    return body
