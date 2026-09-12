"""GitHub adapters over HTTP: OAuth (web flow) and the data API (GraphQL + REST)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from app.application.errors import (
    ActionsUnavailable,
    AuthenticationError,
    GitHubUnavailable,
    RateLimited,
)
from app.application.ports import RepositoryPage
from app.domain.models import (
    Issue,
    PullRequest,
    RateLimit,
    Repository,
    RunStatus,
    User,
    WorkflowRun,
)

log = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
_PAGE_SIZE = 50
_DETAIL_ITEMS = 10

_REPOSITORIES_QUERY = """
query Repositories($cursor: String, $pageSize: Int!, $details: Int!) {
  rateLimit { remaining limit resetAt }
  viewer {
    login
    repositories(
      first: $pageSize
      after: $cursor
      ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
      affiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]
      orderBy: { field: PUSHED_AT, direction: DESC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        name
        owner { login }
        url
        description
        isPrivate
        isArchived
        isFork
        hasIssuesEnabled
        stargazerCount
        pushedAt
        primaryLanguage { name color }
        defaultBranchRef { name }
        pullRequests(
          states: OPEN, first: $details, orderBy: { field: UPDATED_AT, direction: DESC }
        ) {
          totalCount
          nodes { number title url isDraft updatedAt author { login } }
        }
        issues(
          states: OPEN, first: $details, orderBy: { field: UPDATED_AT, direction: DESC }
        ) {
          totalCount
          nodes { number title url updatedAt author { login } }
        }
      }
    }
  }
}
"""


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    status = response.status_code
    if status == 401:
        raise AuthenticationError(context)
    if status in (403, 429):
        remaining = response.headers.get("x-ratelimit-remaining")
        if remaining == "0" or "rate limit" in response.text.lower():
            raise RateLimited(context)
    if status >= 400:
        raise GitHubUnavailable(f"{context}: HTTP {status}")


class GitHubHttpOAuth:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        client_secret: str,
        callback_url: str,
        scopes: str,
        api_url: str,
        web_url: str,
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url
        self._scopes = scopes
        self._api_url = api_url
        self._web_url = web_url

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._callback_url,
                "scope": self._scopes,
                "state": state,
                "allow_signup": "true",
            }
        )
        return f"{self._web_url}/login/oauth/authorize?{query}"

    async def exchange_code(self, code: str) -> str:
        try:
            response = await self._client.post(
                f"{self._web_url}/login/oauth/access_token",
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "redirect_uri": self._callback_url,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("token exchange failed") from exc
        if response.status_code >= 400:
            raise GitHubUnavailable(f"token exchange: HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            log.warning("token exchange rejected: %s", payload.get("error", "unknown"))
            raise AuthenticationError(payload.get("error", "no access token"))
        return token

    async def fetch_viewer(self, token: str) -> User:
        try:
            response = await self._client.get(f"{self._api_url}/user", headers=_headers(token))
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("fetching user failed") from exc
        _raise_for_status(response, context="fetch user")
        data = response.json()
        return User(
            id=int(data["id"]),
            login=data["login"],
            name=data.get("name"),
            avatar_url=data.get("avatar_url", ""),
            html_url=data.get("html_url", ""),
        )

    async def revoke_token(self, token: str) -> None:
        try:
            response = await self._client.request(
                "DELETE",
                f"{self._api_url}/applications/{self._client_id}/token",
                auth=(self._client_id, self._client_secret),
                json={"access_token": token},
                headers={"Accept": "application/vnd.github+json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("token revocation failed") from exc
        if response.status_code not in (204, 404, 422):
            raise GitHubUnavailable(f"token revocation: HTTP {response.status_code}")


class GitHubHttpApi:
    def __init__(self, client: httpx.AsyncClient, *, api_url: str) -> None:
        self._client = client
        self._api_url = api_url

    async def list_repositories(self, token: str) -> RepositoryPage:
        repositories: list[Repository] = []
        rate_limit: RateLimit | None = None
        cursor: str | None = None
        while True:
            data = await self._graphql(
                token,
                _REPOSITORIES_QUERY,
                {"cursor": cursor, "pageSize": _PAGE_SIZE, "details": _DETAIL_ITEMS},
            )
            rl = data.get("rateLimit")
            if rl:
                rate_limit = RateLimit(rl["remaining"], rl["limit"], _parse_dt(rl.get("resetAt")))
            connection = data["viewer"]["repositories"]
            repositories.extend(_repository_from_node(n) for n in connection["nodes"] if n)
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            cursor = page["endCursor"]
        return RepositoryPage(tuple(repositories), rate_limit)

    async def list_recent_runs(
        self, token: str, owner: str, name: str, limit: int
    ) -> list[WorkflowRun]:
        try:
            response = await self._client.get(
                f"{self._api_url}/repos/{owner}/{name}/actions/runs",
                params={"per_page": limit},
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(f"runs {owner}/{name}") from exc
        if response.status_code == 404:
            raise ActionsUnavailable("disabled")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") != "0":
            raise ActionsUnavailable("forbidden")
        _raise_for_status(response, context=f"runs {owner}/{name}")
        return [_run_from_json(r) for r in response.json().get("workflow_runs", [])]

    async def _graphql(self, token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._api_url}/graphql",
                json={"query": query, "variables": variables},
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("graphql request failed") from exc
        _raise_for_status(response, context="graphql")
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            types = {e.get("type") for e in errors}
            if "RATE_LIMITED" in types:
                raise RateLimited("graphql")
            messages = "; ".join(e.get("message", "?") for e in errors)
            if payload.get("data") is None:
                raise GitHubUnavailable(f"graphql: {messages}")
            log.warning("graphql partial errors: %s", messages)
        return payload["data"]


def _repository_from_node(node: dict[str, Any]) -> Repository:
    language = node.get("primaryLanguage") or {}
    default_branch = node.get("defaultBranchRef") or {}
    prs = node["pullRequests"]
    issues = node["issues"]
    return Repository(
        full_name=node["nameWithOwner"],
        name=node["name"],
        owner=node["owner"]["login"],
        url=node["url"],
        description=node.get("description"),
        is_private=bool(node["isPrivate"]),
        is_archived=bool(node["isArchived"]),
        is_fork=bool(node["isFork"]),
        has_issues=bool(node["hasIssuesEnabled"]),
        stars=int(node["stargazerCount"]),
        pushed_at=_parse_dt(node.get("pushedAt")),
        language=language.get("name"),
        language_color=language.get("color"),
        default_branch=default_branch.get("name"),
        open_pr_count=int(prs["totalCount"]),
        open_issue_count=int(issues["totalCount"]),
        pull_requests=tuple(
            PullRequest(
                number=p["number"],
                title=p["title"],
                url=p["url"],
                author=(p.get("author") or {}).get("login"),
                is_draft=bool(p["isDraft"]),
                updated_at=_parse_dt(p["updatedAt"]),  # type: ignore[arg-type]
            )
            for p in prs["nodes"]
            if p
        ),
        issues=tuple(
            Issue(
                number=i["number"],
                title=i["title"],
                url=i["url"],
                author=(i.get("author") or {}).get("login"),
                updated_at=_parse_dt(i["updatedAt"]),  # type: ignore[arg-type]
            )
            for i in issues["nodes"]
            if i
        ),
    )


def _run_status(status: str | None, conclusion: str | None) -> RunStatus:
    if status in ("queued", "waiting", "pending", "requested"):
        return RunStatus.QUEUED
    if status == "in_progress":
        return RunStatus.IN_PROGRESS
    try:
        return RunStatus(conclusion or "unknown")
    except ValueError:
        return RunStatus.UNKNOWN


def _run_from_json(data: dict[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=int(data["id"]),
        workflow_name=data.get("name") or data.get("path") or "workflow",
        title=data.get("display_title") or data.get("name") or "",
        url=data.get("html_url", ""),
        branch=data.get("head_branch"),
        event=data.get("event", ""),
        status=_run_status(data.get("status"), data.get("conclusion")),
        run_number=int(data.get("run_number", 0)),
        created_at=_parse_dt(data.get("created_at")) or datetime.now(UTC),
        updated_at=_parse_dt(data.get("updated_at")) or datetime.now(UTC),
    )
