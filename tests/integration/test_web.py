from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.domain.models import RunStatus
from app.infrastructure.settings import Settings
from app.main import create_app
from app.web.container import Container
from app.web.security import SESSION_COOKIE, STATE_COOKIE
from tests.fakes import (
    FakeApi,
    FakeCache,
    FakeOAuth,
    FakeSessions,
    PlainCipher,
    make_repo,
    make_run,
)

SETTINGS_ENV = {
    "GITHUB_CLIENT_ID": "cid",
    "GITHUB_CLIENT_SECRET": "sec",
    "SECRET_KEY": "x" * 48,
    "BASE_URL": "http://testserver",
    "CACHE_TTL_SECONDS": "60",
}


@pytest.fixture
def fakes():
    api = FakeApi(
        repos=[make_repo("red", prs=2, issues=1), make_repo("green")],
        runs={
            "octocat/red": [make_run(RunStatus.FAILURE)],
            "octocat/green": [make_run(RunStatus.SUCCESS)],
        },
    )
    return {
        "oauth": FakeOAuth(),
        "api": api,
        "sessions": FakeSessions(),
        "cache": FakeCache(),
        "cipher": PlainCipher(),
    }


@pytest.fixture
def client(fakes):
    settings = Settings.from_env(SETTINGS_ENV)
    container = Container.assemble(settings, **fakes)
    with TestClient(create_app(container), follow_redirects=False) as c:
        yield c


def sign_in(client: TestClient) -> None:
    start = client.get("/auth/github")
    assert start.status_code == 303
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.cookies.get(STATE_COOKIE) == state
    done = client.get("/auth/callback", params={"code": "abc", "state": state})
    assert done.status_code == 303 and done.headers["location"] == "/"
    assert SESSION_COOKIE in client.cookies


def test_anonymous_is_redirected_and_api_is_locked(client):
    assert client.get("/").status_code == 303
    assert client.get("/").headers["location"] == "/login"
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/me").status_code == 401
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_login_page_renders_with_security_headers(client):
    page = client.get("/login", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert page.status_code == 200
    assert 'lang="de"' in page.text
    assert "/auth/github" in page.text
    assert "Content-Security-Policy" in page.headers
    assert page.headers["X-Frame-Options"] == "DENY"


def test_login_page_shows_known_errors_only(client):
    assert 'data-i18n="error_state"' in client.get("/login?error=state").text
    assert "error_" not in client.get("/login?error=<script>").text


def test_full_login_overview_logout_cycle(client, fakes):
    sign_in(client)

    page = client.get("/")
    assert page.status_code == 200
    assert "octocat" in page.text

    me = client.get("/api/me").json()
    assert me["user"]["login"] == "octocat"

    overview = client.get("/api/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body["totals"]["open_prs"] == 2
    assert body["totals"]["failed_runs"] == 1
    assert body["repos"][0]["repository"]["name"] == "red"
    assert body["from_cache"] is False
    assert overview.headers["Cache-Control"] == "no-store"

    assert client.get("/api/overview").json()["from_cache"] is True
    assert client.get("/api/overview?refresh=true").json()["from_cache"] is False

    out = client.post("/logout")
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert fakes["oauth"].revoked == ["gho_test"]
    assert fakes["sessions"].records == {}
    assert client.get("/api/me").status_code == 401


def test_callback_rejects_bad_state_and_denied(client, fakes):
    client.get("/auth/github")
    bad = client.get("/auth/callback", params={"code": "abc", "state": "forged"})
    assert bad.headers["location"] == "/login?error=state"
    assert fakes["oauth"].exchanged == []

    denied = client.get("/auth/callback", params={"error": "access_denied"})
    assert denied.headers["location"] == "/login?error=denied"


def test_callback_reports_rejected_code(client, fakes):
    fakes["oauth"].reject_code = True
    start = client.get("/auth/github")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = client.get("/auth/callback", params={"code": "abc", "state": state})
    assert done.headers["location"] == "/login?error=code"


def test_allow_list_blocks_other_accounts(fakes):
    settings = Settings.from_env({**SETTINGS_ENV, "ALLOWED_LOGINS": "someone-else"})
    container = Container.assemble(settings, **fakes)
    with TestClient(create_app(container), follow_redirects=False) as client:
        start = client.get("/auth/github")
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = client.get("/auth/callback", params={"code": "abc", "state": state})
        assert done.headers["location"] == "/login?error=forbidden"
        assert fakes["oauth"].revoked == ["gho_test"]


def test_revoked_token_clears_session(client, fakes):
    sign_in(client)
    fakes["api"].token_valid = False
    response = client.get("/api/overview")
    assert response.status_code == 401
    assert fakes["sessions"].records == {}


def test_tampered_cookie_is_ignored(client):
    sign_in(client)
    client.cookies.set(SESSION_COOKIE, "not-a-signed-value")
    assert client.get("/api/me").status_code == 401
