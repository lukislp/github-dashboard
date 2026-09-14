from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.application.errors import AccessDenied, ActionsUnavailable, RunNotRerunnable
from app.application.ports import RepoItemPage
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
    FakeUserState,
    PlainCipher,
    make_issue,
    make_pr,
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
        "user_state": FakeUserState(),
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


# -- per-user state: preferences and changes-since-last-visit -----------------------------


def test_overview_has_empty_preferences_and_changes_on_first_visit(client):
    sign_in(client)
    body = client.get("/api/overview").json()

    assert body["preferences"] == {"groups": [], "favorites": []}
    assert body["changes"]["since"] is None
    assert body["changes"]["total"] == 0
    assert body["changes"]["new_prs"] == []


def test_preferences_and_seen_endpoints_require_a_session(client):
    assert client.get("/api/preferences").status_code == 401
    assert client.put("/api/preferences", json={"groups": [], "favorites": []}).status_code == 401
    assert client.post("/api/seen").status_code == 401


def test_put_preferences_roundtrips_and_is_reflected_in_overview(client):
    sign_in(client)
    payload = {
        "groups": [{"name": "Backend", "repos": ["octocat/red"]}],
        "favorites": ["octocat/red"],
    }

    put = client.put("/api/preferences", json=payload)
    assert put.status_code == 200
    assert put.json() == {
        "groups": [{"name": "Backend", "repos": ["octocat/red"]}],
        "favorites": ["octocat/red"],
    }

    assert client.get("/api/preferences").json() == put.json()
    assert client.get("/api/overview").json()["preferences"] == put.json()


def test_put_preferences_rejects_invalid_input(client):
    sign_in(client)
    response = client.put("/api/preferences", json={"groups": [], "favorites": ["not-a-repo"]})

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_preferences"
    assert "invalid repository name" in body["detail"]


def test_put_preferences_rejects_malformed_json_without_leaking_parser_detail(client):
    sign_in(client)
    response = client.put(
        "/api/preferences",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body == {"error": "invalid_json"}
    # The json module's message ("Expecting property name ... line 1 column 2 (char 1)")
    # quotes the payload and its offsets - it must not travel to the client.
    assert "detail" not in body


def test_put_preferences_rejects_wrong_shape_without_detail(client):
    sign_in(client)
    response = client.put("/api/preferences", json={"groups": "Backend"})

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_preferences"}


def test_mark_seen_then_new_pr_shows_up_in_next_changes(client, fakes):
    sign_in(client)
    client.get("/api/overview")  # populate the cache

    seen = client.post("/api/seen")
    assert seen.status_code == 200
    assert "seen_at" in seen.json()

    # A new pull request appears on the "green" repository since the snapshot was taken.
    fakes["api"].repos = [make_repo("red", prs=2, issues=1), make_repo("green", prs=1)]

    changed = client.get("/api/overview?refresh=true").json()
    assert changed["changes"]["since"] is not None
    assert changed["changes"]["total"] == 1
    assert changed["changes"]["new_prs"][0]["repo_full_name"] == "octocat/green"
    assert changed["changes"]["new_prs"][0]["number"] == 1


def test_csrf_guard_rejects_cross_site_put_and_post(client):
    sign_in(client)
    headers = {"Sec-Fetch-Site": "cross-site"}

    put = client.put("/api/preferences", json={"groups": [], "favorites": []}, headers=headers)
    assert put.status_code == 403
    assert put.json() == {"error": "cross_site"}

    post = client.post("/api/seen", headers=headers)
    assert post.status_code == 403
    assert post.json() == {"error": "cross_site"}


def test_csrf_guard_allows_same_origin_and_no_header_requests(client):
    sign_in(client)

    same_origin = client.put(
        "/api/preferences",
        json={"groups": [], "favorites": []},
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert same_origin.status_code == 200

    no_headers = client.post("/api/seen")
    assert no_headers.status_code == 200


# -- rerun failed jobs ------------------------------------------------------------------------


def test_rerun_run_success_returns_202_and_invalidates_cache(client, fakes):
    sign_in(client)
    client.get("/api/overview")  # populate the cache
    assert fakes["cache"].entries

    response = client.post("/api/repos/octocat/red/runs/1/rerun")

    assert response.status_code == 202
    assert response.json() == {"status": "queued"}
    assert fakes["api"].rerun_calls == [("octocat/red", 1)]
    assert fakes["cache"].entries == {}


def test_rerun_run_forbidden(client, fakes):
    sign_in(client)
    fakes["api"].rerun_error = AccessDenied("forbidden")

    response = client.post("/api/repos/octocat/red/runs/1/rerun")

    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}


def test_rerun_run_not_rerunnable(client, fakes):
    sign_in(client)
    fakes["api"].rerun_error = RunNotRerunnable("still running")

    response = client.post("/api/repos/octocat/red/runs/1/rerun")

    assert response.status_code == 409
    assert response.json() == {"error": "not_rerunnable"}


def test_rerun_run_actions_unavailable(client, fakes):
    sign_in(client)
    fakes["api"].rerun_error = ActionsUnavailable("disabled")

    response = client.post("/api/repos/octocat/red/runs/1/rerun")

    assert response.status_code == 404
    assert response.json() == {"error": "actions_unavailable"}


def test_rerun_run_rejects_bad_owner_or_name(client):
    sign_in(client)

    response = client.post("/api/repos/bad%21owner/red/runs/1/rerun")

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_repository"}


def test_rerun_run_requires_a_session(client):
    response = client.post("/api/repos/octocat/red/runs/1/rerun")
    assert response.status_code == 401


def test_rerun_run_rejects_cross_site(client):
    sign_in(client)

    response = client.post(
        "/api/repos/octocat/red/runs/1/rerun", headers={"Sec-Fetch-Site": "cross-site"}
    )

    assert response.status_code == 403
    assert response.json() == {"error": "cross_site"}


# -- paginated pull request / issue listing ----------------------------------------------------


def test_list_items_returns_pull_requests_marked_like_the_overview(client, fakes):
    sign_in(client)
    pr = make_pr(1)
    fakes["api"].repo_items = {
        ("octocat/red", "pull_requests"): RepoItemPage(pull_requests=(pr,), next_cursor="c2")
    }

    response = client.get("/api/repos/octocat/red/items", params={"kind": "pull_requests"})

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] == "c2"
    assert body["issues"] == []
    assert len(body["pull_requests"]) == 1
    assert body["pull_requests"][0]["number"] == 1
    assert "stale" in body["pull_requests"][0]
    assert "age_days" in body["pull_requests"][0]
    assert fakes["api"].repo_items_calls == [("octocat/red", "pull_requests", None, 50)]


def test_list_items_returns_issues_with_a_cursor(client, fakes):
    sign_in(client)
    issue = make_issue(1)
    fakes["api"].repo_items = {("octocat/red", "issues"): RepoItemPage(issues=(issue,))}

    response = client.get(
        "/api/repos/octocat/red/items", params={"kind": "issues", "cursor": "abc"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pull_requests"] == []
    assert len(body["issues"]) == 1
    assert fakes["api"].repo_items_calls == [("octocat/red", "issues", "abc", 50)]


def test_list_items_rejects_invalid_kind(client):
    sign_in(client)
    response = client.get("/api/repos/octocat/red/items", params={"kind": "bogus"})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_kind"}


def test_list_items_rejects_bad_owner_or_name(client):
    sign_in(client)
    response = client.get("/api/repos/bad%21owner/red/items", params={"kind": "issues"})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_repository"}


def test_list_items_requires_a_session(client):
    response = client.get("/api/repos/octocat/red/items", params={"kind": "issues"})
    assert response.status_code == 401


def test_bundled_fonts_are_served_with_the_right_media_type(client):
    """A woff2 served as application/octet-stream still renders, but it defeats caching and
    compression heuristics - and Python's mimetypes table does not know the type everywhere."""
    response = client.get("/static/fonts/ibm-plex-sans-400-latin.woff2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
