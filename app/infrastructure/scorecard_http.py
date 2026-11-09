"""OpenSSF Scorecard adapter: the public REST API of api.securityscorecards.dev (no token)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.application.errors import ScorecardUnavailable
from app.domain.scorecard import NOT_APPLICABLE, Scorecard, ScorecardCheck

DEFAULT_API_URL = "https://api.securityscorecards.dev"
# Short on purpose: the score is a garnish, and a slow Scorecard must not hold up the page.
REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
_MAX_CHECKS = 50
_MAX_DETAILS = 100
_MAX_TEXT = 2000


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _opt_text(value: Any) -> str | None:
    return _text(value) or None


def _score(value: Any, *, lowest: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("score is not a number")
    if not lowest <= value <= 10:
        raise ValueError("score out of range")
    return float(value)


def _doc_url(documentation: Any) -> str | None:
    url = documentation.get("url") if isinstance(documentation, dict) else None
    # Rendered as a link in the browser: only ever http(s).
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else None


def parse_scorecard(payload: Any) -> Scorecard:
    """Turn the API's JSON into a Scorecard. Raises ValueError on anything unusable."""
    if not isinstance(payload, dict):
        raise ValueError("not an object")
    raw_checks = payload.get("checks")
    if not isinstance(raw_checks, list) or len(raw_checks) > _MAX_CHECKS:
        raise ValueError("checks missing")
    checks = []
    for raw in raw_checks:
        if not isinstance(raw, dict) or not _text(raw.get("name")):
            raise ValueError("bad check")
        score = _score(raw.get("score"), lowest=NOT_APPLICABLE)
        details = raw.get("details")
        documentation = raw.get("documentation")
        checks.append(
            ScorecardCheck(
                name=_text(raw["name"], 100),
                score=int(score),
                reason=_text(raw.get("reason")),
                details=tuple(
                    _text(line)
                    for line in (details[:_MAX_DETAILS] if isinstance(details, list) else [])
                    if isinstance(line, str)
                ),
                doc_url=_doc_url(documentation),
                doc_short=_opt_text(
                    documentation.get("short") if isinstance(documentation, dict) else None
                ),
            )
        )
    scorecard = payload.get("scorecard")
    return Scorecard(
        score=_score(payload.get("score"), lowest=0),
        scanned_at=_opt_text(payload.get("date")),
        version=_opt_text(scorecard.get("version") if isinstance(scorecard, dict) else None),
        checks=tuple(checks),
    )


class ScorecardHttpApi:
    def __init__(self, http: httpx.AsyncClient, api_url: str = DEFAULT_API_URL) -> None:
        self._http = http
        self._api_url = api_url.rstrip("/")

    async def fetch(self, owner: str, name: str) -> Scorecard | None:
        url = f"{self._api_url}/projects/github.com/{quote(owner, safe='')}/{quote(name, safe='')}"
        try:
            response = await self._http.get(
                url, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise ScorecardUnavailable("scorecard request failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ScorecardUnavailable(f"scorecard answered {response.status_code}")
        try:
            return parse_scorecard(response.json())
        except ValueError as exc:
            raise ScorecardUnavailable("scorecard answer is not a result") from exc
