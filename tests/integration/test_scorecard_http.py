import httpx
import pytest
import respx

from app.application.errors import ScorecardUnavailable
from app.infrastructure.scorecard_http import ScorecardHttpApi

API = "https://scorecard.test"
URL = f"{API}/projects/github.com/octocat/red"


def payload(**overrides):
    body = {
        "score": 7.6,
        "date": "2026-11-08T03:00:00Z",
        "scorecard": {"version": "v5.1.0"},
        "checks": [
            {
                "name": "Fuzzing",
                "score": 0,
                "reason": "no fuzzer detected",
                "details": ["Warn: no fuzzer integrations found"],
                "documentation": {"url": "https://d/fuzz", "short": "Determines fuzzing."},
            },
            {
                "name": "Packaging",
                "score": -1,
                "reason": "n/a",
                "details": None,
                "documentation": {},
            },
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
async def api():
    async with httpx.AsyncClient() as http:
        yield ScorecardHttpApi(http, api_url=API)


@respx.mock
async def test_parses_a_result(api):
    route = respx.get(URL).respond(200, json=payload())
    card = await api.fetch("octocat", "red")
    assert route.called
    assert card.score == 7.6
    assert card.scanned_at == "2026-11-08T03:00:00Z"
    assert card.version == "v5.1.0"
    fuzzing, packaging = card.checks
    assert (fuzzing.name, fuzzing.score, fuzzing.details) == (
        "Fuzzing",
        0,
        ("Warn: no fuzzer integrations found",),
    )
    assert fuzzing.doc_url == "https://d/fuzz" and fuzzing.doc_short == "Determines fuzzing."
    assert packaging.score == -1 and packaging.details == () and packaging.doc_url is None


@respx.mock
async def test_404_means_never_scored(api):
    respx.get(URL).respond(404, json={"message": "not found"})
    assert await api.fetch("octocat", "red") is None


@respx.mock
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_error_statuses_are_unavailable(api, status):
    respx.get(URL).respond(status)
    with pytest.raises(ScorecardUnavailable):
        await api.fetch("octocat", "red")


@respx.mock
@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])
async def test_timeout_and_connection_errors_are_unavailable(api, error):
    respx.get(URL).mock(side_effect=error)
    with pytest.raises(ScorecardUnavailable):
        await api.fetch("octocat", "red")


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"checks": []}',
        b'{"score": 1}',
        None,
    ],
)
async def test_malformed_json_is_unavailable(api, body):
    respx.get(URL).respond(200, content=body or b"")
    with pytest.raises(ScorecardUnavailable):
        await api.fetch("octocat", "red")


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        payload(score="high"),
        payload(score=11),
        payload(score=True),
        payload(checks="none"),
        payload(checks=[{"score": 3}]),
        payload(checks=[{"name": "X", "score": 12}]),
    ],
)
async def test_unusable_results_are_unavailable(api, body):
    respx.get(URL).respond(200, json=body)
    with pytest.raises(ScorecardUnavailable):
        await api.fetch("octocat", "red")


@respx.mock
async def test_unsafe_documentation_link_is_dropped(api):
    bad = payload()
    bad["checks"][0]["documentation"]["url"] = "javascript:alert(1)"
    respx.get(URL).respond(200, json=bad)
    card = await api.fetch("octocat", "red")
    assert card.checks[0].doc_url is None


@respx.mock
async def test_path_segments_are_quoted(api):
    route = respx.get(f"{API}/projects/github.com/a%2Fb/c").respond(404)
    await api.fetch("a/b", "c")
    assert route.called
