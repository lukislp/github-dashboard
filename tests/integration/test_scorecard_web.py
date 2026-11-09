import pytest
from fastapi.testclient import TestClient

from app.application.errors import ScorecardUnavailable
from app.domain.models import RunStatus
from app.infrastructure.settings import Settings
from app.main import create_app
from app.web.container import Container
from tests.fakes import (
    FakeApi,
    FakeCache,
    FakeOAuth,
    FakeScorecardApi,
    FakeSessions,
    FakeUserState,
    PlainCipher,
    make_hygiene_facts,
    make_repo,
    make_run,
    make_scorecard,
)
from tests.integration.test_web import SETTINGS_ENV, sign_in

NAMES = ("red", "green", "hidden", "fresh")


@pytest.fixture
def fakes():
    return {
        "oauth": FakeOAuth(),
        "api": FakeApi(
            repos=[
                make_repo("red"),
                make_repo("green"),
                make_repo("hidden", private=True),
                make_repo("fresh"),
            ],
            runs={f"octocat/{n}": [make_run(RunStatus.SUCCESS)] for n in NAMES},
            hygiene_by_repo={f"octocat/{n}": make_hygiene_facts() for n in NAMES},
        ),
        "sessions": FakeSessions(),
        "cache": FakeCache(),
        "cipher": PlainCipher(),
        "user_state": FakeUserState(),
        "scorecard_api": FakeScorecardApi(
            {
                "octocat/red": make_scorecard(8.4),
                "octocat/green": make_scorecard(4.0),
                "octocat/hidden": make_scorecard(9.9),
            }
        ),
    }


@pytest.fixture
def client(fakes):
    container = Container.assemble(Settings.from_env(SETTINGS_ENV), **fakes)
    with TestClient(create_app(container), follow_redirects=False) as c:
        yield c


def test_endpoints_require_a_session(client):
    assert client.get("/api/scorecards").status_code == 401
    assert client.get("/api/repos/octocat/red/scorecard").status_code == 401


def test_summary_covers_public_repositories_of_the_overview_only(client, fakes):
    sign_in(client)
    assert client.get("/api/scorecards").json()["scorecards"] == {}  # no overview yet
    assert client.get("/api/overview").status_code == 200
    body = client.get("/api/scorecards").json()
    assert body["enabled"] is True
    assert body["scorecards"] == {
        "octocat/fresh": {"state": "none"},
        "octocat/green": {"state": "scored", "score": 4.0, "band": "critical"},
        "octocat/red": {"state": "scored", "score": 8.4, "band": "good"},
    }
    assert "octocat/hidden" not in fakes["scorecard_api"].calls


def test_unreachable_scorecard_degrades_to_unknown(client, fakes):
    fakes["scorecard_api"].results["octocat/red"] = ScorecardUnavailable("down")
    sign_in(client)
    client.get("/api/overview")
    response = client.get("/api/scorecards")
    assert response.status_code == 200
    assert response.json()["scorecards"]["octocat/red"] == {"state": "unknown"}
    assert response.json()["scorecards"]["octocat/green"]["state"] == "scored"


def test_detail_lists_checks_weakest_first(client):
    sign_in(client)
    body = client.get("/api/repos/octocat/red/scorecard").json()
    assert body["score"] == 8.4 and body["band"] == "good"
    assert [c["name"] for c in body["checks"]] == [
        "Fuzzing",
        "Branch-Protection",
        "Pinned-Dependencies",
        "Packaging",
    ]
    assert body["viewer_url"] == "https://scorecard.dev/viewer/?uri=github.com/octocat/red"
    assert client.get("/api/repos/octocat/fresh/scorecard").json()["state"] == "none"


def test_detail_rejects_malformed_names(client):
    sign_in(client)
    assert client.get("/api/repos/octocat/a%20b/scorecard").status_code == 400


def test_switched_off_never_asks_scorecard(fakes):
    settings = Settings.from_env({**SETTINGS_ENV, "SCORECARDS": "false"})
    container = Container.assemble(settings, **fakes)
    with TestClient(create_app(container), follow_redirects=False) as client:
        sign_in(client)
        assert client.get("/api/scorecards").json() == {"enabled": False, "scorecards": {}}
        assert client.get("/api/repos/octocat/red/scorecard").status_code == 404
    assert fakes["scorecard_api"].calls == []


def test_without_adapter_the_feature_is_off(fakes):
    del fakes["scorecard_api"]
    container = Container.assemble(Settings.from_env(SETTINGS_ENV), **fakes)
    with TestClient(create_app(container), follow_redirects=False) as client:
        sign_in(client)
        assert client.get("/api/scorecards").json()["enabled"] is False
