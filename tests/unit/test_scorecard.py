import pytest

from app.application.errors import ScorecardUnavailable
from app.application.use_cases import GetScorecards
from app.domain.scorecard import (
    NOT_SCORED,
    ScorecardLookup,
    ScorecardState,
    band,
    detail_to_dict,
    summary_to_dict,
    viewer_url,
    weakest_first,
)
from app.infrastructure.cache_memory import MemoryScorecardCache
from tests.fakes import FakeScorecardApi, make_scorecard


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (10, "good"),
        (8.0, "good"),
        (7.9, "warning"),
        (6.0, "warning"),
        (5.9, "critical"),
        (0, "critical"),
    ],
)
def test_band_boundaries(score, expected):
    assert band(score) == expected


def test_weakest_first_puts_not_applicable_last():
    ordered = weakest_first(make_scorecard().checks)
    assert [c.name for c in ordered] == [
        "Fuzzing",
        "Branch-Protection",
        "Pinned-Dependencies",
        "Packaging",
    ]


def test_summary_and_detail_shape():
    lookup = ScorecardLookup(ScorecardState.SCORED, make_scorecard(7.6))
    assert summary_to_dict(lookup) == {"state": "scored", "score": 7.6, "band": "warning"}
    detail = detail_to_dict("octocat", "red", lookup)
    assert detail["viewer_url"] == "https://scorecard.dev/viewer/?uri=github.com/octocat/red"
    assert detail["scanned_at"] == "2026-11-08T03:00:00Z"
    assert detail["version"] == "v5.1.0"
    assert detail["checks"][0]["name"] == "Fuzzing"
    assert detail["checks"][0]["band"] == "critical"
    assert detail["checks"][-1]["band"] is None
    assert detail_to_dict("o", "n", NOT_SCORED) == {
        "state": "none",
        "viewer_url": viewer_url("o", "n"),
    }


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_lookup_caches_scored_and_not_scored_until_ttl():
    api = FakeScorecardApi({"o/a": make_scorecard()})
    clock = Clock()
    use_case = GetScorecards(api=api, cache=MemoryScorecardCache(monotonic=clock), ttl_seconds=100)
    first = await use_case.lookup("o", "A")
    assert first.state is ScorecardState.SCORED
    assert (await use_case.lookup("o", "a")).scorecard == first.scorecard
    assert (await use_case.lookup("o", "never")).state is ScorecardState.NONE
    await use_case.lookup("o", "never")
    assert api.calls == ["o/A", "o/never"]
    clock.now = 101
    await use_case.lookup("o", "a")
    assert api.calls == ["o/A", "o/never", "o/a"]


async def test_failure_is_unknown_and_remembered_only_briefly():
    api = FakeScorecardApi({"o/a": ScorecardUnavailable("down")})
    clock = Clock()
    use_case = GetScorecards(
        api=api, cache=MemoryScorecardCache(monotonic=clock), failure_ttl_seconds=30
    )
    assert (await use_case.lookup("o", "a")).state is ScorecardState.UNKNOWN
    await use_case.lookup("o", "a")
    assert len(api.calls) == 1
    clock.now = 31
    api.results["o/a"] = make_scorecard()
    assert (await use_case.lookup("o", "a")).state is ScorecardState.SCORED


async def test_disabled_or_missing_adapter_never_asks():
    api = FakeScorecardApi({"o/a": make_scorecard()})
    off = GetScorecards(api=api, cache=MemoryScorecardCache(), enabled=False)
    assert not off.active
    assert (await off.lookup("o", "a")).state is ScorecardState.UNKNOWN
    assert api.calls == []
    assert not GetScorecards(api=None, cache=MemoryScorecardCache()).active


async def test_lookup_many_bounds_concurrency():
    names = [f"o/r{i}" for i in range(20)]
    api = FakeScorecardApi({n: make_scorecard() for n in names})
    use_case = GetScorecards(api=api, cache=MemoryScorecardCache(), max_concurrency=3)
    result = await use_case.lookup_many(names)
    assert set(result) == set(names)
    assert 1 < api.max_in_flight <= 3


async def test_memory_cache_is_bounded():
    cache = MemoryScorecardCache(max_entries=2)
    for key in ("a", "b", "c"):
        await cache.set(key, NOT_SCORED, 100)
    assert await cache.get("a") is None
    assert await cache.get("c") is NOT_SCORED
