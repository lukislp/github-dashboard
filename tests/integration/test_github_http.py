import json

import httpx
import pytest
import respx

from app.application.errors import ActionsUnavailable, AuthenticationError, RateLimited
from app.domain.models import RunStatus
from app.infrastructure.github_http import GitHubHttpApi, GitHubHttpOAuth

API = "https://api.github.test"
WEB = "https://github.test"


def repo_node(name: str, *, prs: int = 0, issues: int = 0) -> dict:
    return {
        "nameWithOwner": f"octocat/{name}",
        "name": name,
        "owner": {"login": "octocat"},
        "url": f"{WEB}/octocat/{name}",
        "description": None,
        "isPrivate": False,
        "isArchived": False,
        "isFork": False,
        "hasIssuesEnabled": True,
        "stargazerCount": 1,
        "pushedAt": "2026-09-01T10:00:00Z",
        "primaryLanguage": {"name": "Python", "color": "#3572A5"},
        "defaultBranchRef": {"name": "main"},
        "pullRequests": {
            "totalCount": prs,
            "nodes": [
                {
                    "number": 1,
                    "title": "PR",
                    "url": "u",
                    "isDraft": True,
                    "updatedAt": "2026-09-01T10:00:00Z",
                    "author": {"login": "bob"},
                }
            ][:prs],
        },
        "issues": {
            "totalCount": issues,
            "nodes": [
                {
                    "number": 2,
                    "title": "Bug",
                    "url": "u",
                    "updatedAt": "2026-09-01T10:00:00Z",
                    "author": None,
                }
            ][:issues],
        },
    }


def graphql_page(nodes: list[dict], *, has_next: bool, cursor: str | None) -> dict:
    return {
        "data": {
            "rateLimit": {"remaining": 4900, "limit": 5000, "resetAt": "2026-09-12T13:00:00Z"},
            "viewer": {
                "login": "octocat",
                "repositories": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                },
            },
        }
    }


@pytest.fixture
async def client():
    async with httpx.AsyncClient() as c:
        yield c


@respx.mock
async def test_list_repositories_paginates(client):
    route = respx.post(f"{API}/graphql").mock(
        side_effect=[
            httpx.Response(
                200,
                json=graphql_page([repo_node("a", prs=1, issues=1)], has_next=True, cursor="c1"),
            ),
            httpx.Response(200, json=graphql_page([repo_node("b")], has_next=False, cursor=None)),
        ]
    )
    api = GitHubHttpApi(client, api_url=API)

    page = await api.list_repositories("tok")

    assert route.call_count == 2
    assert [r.full_name for r in page.repositories] == ["octocat/a", "octocat/b"]
    first = page.repositories[0]
    assert first.open_pr_count == 1
    assert first.pull_requests[0].is_draft is True
    assert first.issues[0].author is None
    assert first.pushed_at is not None and first.pushed_at.tzinfo is not None
    assert page.rate_limit.remaining == 4900
    body = json.loads(route.calls[1].request.content)
    assert body["variables"]["cursor"] == "c1"
    assert client.headers.get("Authorization") is None


@respx.mock
async def test_graphql_401_is_authentication_error(client):
    respx.post(f"{API}/graphql").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    with pytest.raises(AuthenticationError):
        await GitHubHttpApi(client, api_url=API).list_repositories("tok")


@respx.mock
async def test_graphql_rate_limited(client):
    respx.post(f"{API}/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": None, "errors": [{"type": "RATE_LIMITED", "message": "x"}]}
        )
    )
    with pytest.raises(RateLimited):
        await GitHubHttpApi(client, api_url=API).list_repositories("tok")


@respx.mock
async def test_list_recent_runs_maps_status(client):
    respx.get(f"{API}/repos/octocat/a/actions/runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "workflow_runs": [
                    {
                        "id": 3,
                        "name": "CI",
                        "display_title": "fix: thing",
                        "html_url": "u3",
                        "head_branch": "main",
                        "event": "push",
                        "status": "in_progress",
                        "conclusion": None,
                        "run_number": 30,
                        "created_at": "2026-09-12T11:00:00Z",
                        "updated_at": "2026-09-12T11:01:00Z",
                    },
                    {
                        "id": 2,
                        "name": "CI",
                        "display_title": "feat: other",
                        "html_url": "u2",
                        "head_branch": "feat",
                        "event": "pull_request",
                        "status": "completed",
                        "conclusion": "failure",
                        "run_number": 29,
                        "created_at": "2026-09-12T10:00:00Z",
                        "updated_at": "2026-09-12T10:05:00Z",
                    },
                    {
                        "id": 1,
                        "name": "CI",
                        "display_title": "x",
                        "html_url": "u1",
                        "head_branch": "main",
                        "event": "push",
                        "status": "completed",
                        "conclusion": "something_new",
                        "run_number": 28,
                        "created_at": "2026-09-12T09:00:00Z",
                        "updated_at": "2026-09-12T09:05:00Z",
                    },
                ]
            },
        )
    )
    runs = await GitHubHttpApi(client, api_url=API).list_recent_runs("tok", "octocat", "a", 5)

    assert [r.status for r in runs] == [RunStatus.IN_PROGRESS, RunStatus.FAILURE, RunStatus.UNKNOWN]
    assert runs[1].failed and runs[0].active
    assert respx.calls.last.request.url.params["per_page"] == "5"


@respx.mock
async def test_list_recent_runs_404_is_unavailable(client):
    respx.get(f"{API}/repos/octocat/a/actions/runs").mock(return_value=httpx.Response(404))
    with pytest.raises(ActionsUnavailable):
        await GitHubHttpApi(client, api_url=API).list_recent_runs("tok", "octocat", "a", 5)


@respx.mock
async def test_oauth_exchange_and_viewer(client):
    respx.post(f"{WEB}/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "gho_x", "token_type": "bearer"})
    )
    respx.get(f"{API}/user").mock(
        return_value=httpx.Response(
            200,
            json={"id": 7, "login": "octocat", "name": None, "avatar_url": "a", "html_url": "h"},
        )
    )
    revoke = respx.delete(f"{API}/applications/cid/token").mock(return_value=httpx.Response(204))
    oauth = GitHubHttpOAuth(
        client,
        client_id="cid",
        client_secret="sec",
        callback_url="http://localhost/auth/callback",
        scopes="repo read:org",
        api_url=API,
        web_url=WEB,
    )

    assert "client_id=cid" in oauth.authorize_url("st") and "state=st" in oauth.authorize_url("st")
    token = await oauth.exchange_code("code")
    user = await oauth.fetch_viewer(token)
    await oauth.revoke_token(token)

    assert token == "gho_x"
    assert user.id == 7 and user.login == "octocat"
    assert revoke.called


@respx.mock
async def test_oauth_exchange_without_token_fails(client):
    respx.post(f"{WEB}/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"error": "bad_verification_code"})
    )
    oauth = GitHubHttpOAuth(
        client,
        client_id="c",
        client_secret="s",
        callback_url="cb",
        scopes="repo",
        api_url=API,
        web_url=WEB,
    )
    with pytest.raises(AuthenticationError):
        await oauth.exchange_code("nope")
