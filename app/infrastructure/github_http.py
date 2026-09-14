"""GitHub adapters over HTTP: OAuth (web flow) and the data API (GraphQL + REST)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.application.errors import (
    AccessDenied,
    ActionsUnavailable,
    AuthenticationError,
    GitHubUnavailable,
    RateLimited,
    RunNotRerunnable,
)
from app.application.ports import BranchListing, HygienePage, RepoItemPage, RepositoryPage, TokenSet
from app.domain.hygiene import HygieneFacts
from app.domain.models import (
    ActionsUsage,
    AttentionItem,
    AttentionKind,
    Branch,
    ChecksState,
    FailedJob,
    Inbox,
    Issue,
    LastCommit,
    Mergeable,
    Notification,
    PullRequest,
    RateLimit,
    ReleaseInfo,
    Repository,
    RepoUsage,
    ReviewDecision,
    RunStatus,
    SeverityCounts,
    User,
    WorkflowRun,
)
from app.domain.pull_requests import is_bot_login
from app.infrastructure.http_cache import ConditionalCache, fingerprint

log = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
# Repository page size: restored to 50 now that hygiene facts (license, vulnerability alerts,
# delete-branch-on-merge, branch protection/rulesets, the file probes) and branch refs are
# fetched by a separate, batched `_HYGIENE_QUERY` instead of being embedded here. Combining
# everything into one query made it slow (7-9s against a real 37-repository account) and
# fragile (transient 502s and RESOURCE_LIMITS_EXCEEDED partial errors even at a reported
# `rateLimit.cost` of only ~13); splitting it keeps this query small and fast, and isolates
# hygiene's cost/latency and failure modes so a hygiene hiccup can never take the whole
# overview down.
_PAGE_SIZE = 50
_PR_DETAIL_ITEMS = 20
_ISSUE_DETAIL_ITEMS = 10
_WORKFLOW_FILE_SUFFIXES = (".yml", ".yaml")
_HYGIENE_BATCH_SIZE = 25
# Branch refs are only fetched by the hygiene query now, which is batched and runs separately
# from the repositories query, so the page limit could be raised from 25 to 50 without
# reintroducing the resource-limit problems the split was meant to fix.
_REFS_PAGE_SIZE = 50
_RETRYABLE_HTTP_STATUSES = frozenset({502, 503, 504})
# `list_run_durations`: at most two pages of 100 runs (GitHub's REST page-size cap), so at
# most 200 runs are ever inspected per repository per month.
_RUN_DURATIONS_PAGE_SIZE = 100
_RUN_DURATIONS_MAX_PAGES = 2
_RESOURCE_LIMITS_EXCEEDED = "RESOURCE_LIMITS_EXCEEDED"
# Job/step conclusions that count as a failure, mirroring `FAILED_STATUSES` for workflow runs.
_JOB_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})

_REPOSITORIES_QUERY = """
query Repositories(
  $cursor: String
  $pageSize: Int!
  $prDetails: Int!
  $issueDetails: Int!
) {
  rateLimit { remaining limit resetAt cost }
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
        id
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
        defaultBranchRef {
          name
          target {
            ... on Commit {
              oid
              messageHeadline
              committedDate
              url
              author { name user { login } }
            }
          }
        }
        pullRequests(
          states: OPEN, first: $prDetails, orderBy: { field: UPDATED_AT, direction: DESC }
        ) {
          totalCount
          nodes {
            number
            title
            url
            isDraft
            updatedAt
            author { login }
            createdAt
            headRefName
            reviewDecision
            mergeable
            commits(last: 1) {
              nodes { commit { statusCheckRollup { state } } }
            }
          }
        }
        issues(
          states: OPEN, first: $issueDetails, orderBy: { field: UPDATED_AT, direction: DESC }
        ) {
          totalCount
          nodes { number title url updatedAt createdAt author { login } }
        }
        vulnerabilityAlerts(states: OPEN, first: 100) {
          totalCount
          nodes { securityVulnerability { severity } }
        }
        latestRelease {
          tagName
          name
          publishedAt
          url
          isPrerelease
        }
      }
    }
  }
}
"""

_HYGIENE_QUERY = """
query Hygiene($ids: [ID!]!, $refsPageSize: Int!) {
  rateLimit { remaining limit resetAt cost }
  nodes(ids: $ids) {
    ... on Repository {
      id
      nameWithOwner
      isFork
      licenseInfo { spdxId name }
      hasVulnerabilityAlertsEnabled
      deleteBranchOnMerge
      branchProtectionRules(first: 1) { totalCount }
      rulesets(first: 1) { totalCount }
      workflowsDir: object(expression: "HEAD:.github/workflows") {
        ... on Tree { entries { name } }
      }
      dependabotYml: object(expression: "HEAD:.github/dependabot.yml") { id }
      dependabotYaml: object(expression: "HEAD:.github/dependabot.yaml") { id }
      renovateJson: object(expression: "HEAD:renovate.json") { id }
      renovateJsonGithub: object(expression: "HEAD:.github/renovate.json") { id }
      renovaterc: object(expression: "HEAD:.renovaterc.json") { id }
      readmeFile: object(expression: "HEAD:README.md") { id }
      securityMd: object(expression: "HEAD:SECURITY.md") { id }
      securityMdGithub: object(expression: "HEAD:.github/SECURITY.md") { id }
      codeownersFile: object(expression: "HEAD:CODEOWNERS") { id }
      codeownersFileGithub: object(expression: "HEAD:.github/CODEOWNERS") { id }
      defaultBranchRef { name }
      refs(refPrefix: "refs/heads/", first: $refsPageSize) {
        totalCount
        nodes {
          name
          target {
            ... on Commit {
              committedDate
              author { name user { login } }
            }
          }
          associatedPullRequests(first: 1) { totalCount }
        }
      }
    }
  }
}
"""

_REPO_PULL_REQUESTS_QUERY = """
query RepoPullRequests($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: OPEN
      first: $pageSize
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        isDraft
        updatedAt
        author { login }
        createdAt
        headRefName
        reviewDecision
        mergeable
        commits(last: 1) {
          nodes { commit { statusCheckRollup { state } } }
        }
      }
    }
  }
}
"""

_REPO_ISSUES_QUERY = """
query RepoIssues($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    issues(
      states: OPEN
      first: $pageSize
      after: $cursor
      orderBy: { field: UPDATED_AT, direction: DESC }
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { number title url updatedAt createdAt author { login } }
    }
  }
}
"""

_ITEMS_QUERY_BY_KIND = {
    "pull_requests": _REPO_PULL_REQUESTS_QUERY,
    "issues": _REPO_ISSUES_QUERY,
}

_INBOX_QUERY = """
query Inbox(
  $reviewRequested: String!
  $changesRequested: String!
  $assigned: String!
  $mentioned: String!
) {
  rateLimit { remaining limit resetAt }
  review_requested: search(type: ISSUE, first: 30, query: $reviewRequested) {
    nodes {
      ... on PullRequest {
        number title url isDraft updatedAt author { login } repository { nameWithOwner }
      }
      ... on Issue { number title url updatedAt author { login } repository { nameWithOwner } }
    }
  }
  changes_requested: search(type: ISSUE, first: 30, query: $changesRequested) {
    nodes {
      ... on PullRequest {
        number title url isDraft updatedAt author { login } repository { nameWithOwner }
      }
      ... on Issue { number title url updatedAt author { login } repository { nameWithOwner } }
    }
  }
  assigned: search(type: ISSUE, first: 30, query: $assigned) {
    nodes {
      ... on PullRequest {
        number title url isDraft updatedAt author { login } repository { nameWithOwner }
      }
      ... on Issue { number title url updatedAt author { login } repository { nameWithOwner } }
    }
  }
  mentioned: search(type: ISSUE, first: 30, query: $mentioned) {
    nodes {
      ... on PullRequest {
        number title url isDraft updatedAt author { login } repository { nameWithOwner }
      }
      ... on Issue { number title url updatedAt author { login } repository { nameWithOwner } }
    }
  }
}
"""

_INBOX_FIELDS: tuple[tuple[str, AttentionKind], ...] = (
    ("review_requested", AttentionKind.REVIEW_REQUESTED),
    ("changes_requested", AttentionKind.CHANGES_REQUESTED),
    ("assigned", AttentionKind.ASSIGNED),
    ("mentioned", AttentionKind.MENTIONED),
)

_CHECKS_STATE_BY_ROLLUP: dict[str, ChecksState] = {
    "SUCCESS": ChecksState.SUCCESS,
    "FAILURE": ChecksState.FAILURE,
    "ERROR": ChecksState.ERROR,
    "PENDING": ChecksState.PENDING,
    "EXPECTED": ChecksState.PENDING,
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class _RetryableHygieneError(Exception):
    """One hygiene batch hit a transient failure (HTTP 502/503/504, or a GraphQL
    RESOURCE_LIMITS_EXCEEDED partial error) and should be retried, split into two halves,
    before its repositories are treated as unavailable for this refresh."""


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


def _cache_key(url: str, params: dict[str, Any] | None) -> str:
    """The URL including its query string, so `?per_page=5` and `?per_page=100` never collide."""
    return str(httpx.URL(url, params=params or {}))


def _is_rate_limited(response: httpx.Response) -> bool:
    if response.status_code not in (403, 429):
        return False
    remaining = response.headers.get("x-ratelimit-remaining")
    return remaining == "0" or "rate limit" in response.text.lower()


def _raise_for_status(response: httpx.Response, *, context: str) -> None:
    status = response.status_code
    if status == 401:
        raise AuthenticationError(context)
    if _is_rate_limited(response):
        raise RateLimited(context)
    if status >= 400:
        raise GitHubUnavailable(f"{context}: HTTP {status}")


def _optional_or_raise(response: httpx.Response, *, context: str) -> bool:
    """For endpoints where 403/404 mean "not available" rather than a real error.

    Returns True when the caller should treat the response as unavailable (`None`).
    Raises AuthenticationError/RateLimited/GitHubUnavailable for genuine failures.
    """
    status = response.status_code
    if status == 401:
        raise AuthenticationError(context)
    if status == 404:
        return True
    if status == 403:
        if _is_rate_limited(response):
            raise RateLimited(context)
        return True
    if status >= 400:
        raise GitHubUnavailable(f"{context}: HTTP {status}")
    return False


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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._client_id = client_id
        self._client_secret = client_secret
        self._callback_url = callback_url
        self._scopes = scopes
        self._api_url = api_url
        self._web_url = web_url
        self._clock = clock

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

    async def exchange_code(self, code: str) -> TokenSet:
        payload = await self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._callback_url,
            },
            context="token exchange",
        )
        return self._token_set_from_payload(payload)

    async def refresh_token(self, refresh_token: str) -> TokenSet:
        payload = await self._request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            context="token refresh",
        )
        return self._token_set_from_payload(payload)

    async def _request_token(self, data: dict[str, str], *, context: str) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._web_url}/login/oauth/access_token",
                data=data,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(f"{context} failed") from exc
        if response.status_code >= 500:
            raise GitHubUnavailable(f"{context}: HTTP {response.status_code}")
        return response.json()

    def _token_set_from_payload(self, payload: dict[str, Any]) -> TokenSet:
        token = payload.get("access_token")
        if not token:
            log.warning("token request rejected: %s", payload.get("error", "unknown"))
            raise AuthenticationError(payload.get("error", "no access token"))
        now = self._clock()
        expires_in = payload.get("expires_in")
        expires_at = now + timedelta(seconds=int(expires_in)) if expires_in is not None else None
        refresh_token = payload.get("refresh_token")
        refresh_expires_in = payload.get("refresh_token_expires_in")
        refresh_expires_at = (
            now + timedelta(seconds=int(refresh_expires_in))
            if refresh_token and refresh_expires_in is not None
            else None
        )
        return TokenSet(
            access_token=token,
            expires_at=expires_at,
            refresh_token=refresh_token,
            refresh_expires_at=refresh_expires_at,
        )

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
    def __init__(
        self, client: httpx.AsyncClient, *, api_url: str, cache: ConditionalCache | None = None
    ) -> None:
        self._client = client
        self._api_url = api_url
        self._cache = cache if cache is not None else ConditionalCache()

    async def _conditional_get(
        self, token: str, url: str, *, params: dict[str, Any] | None = None, context: str
    ) -> httpx.Response:
        """`GET url` with an `If-None-Match` header when the cache already holds an ETag for
        this `(token, url+query)` pair.

        A `304` is served from the cache and returned as a synthetic response carrying the
        cached body, so callers can treat it exactly like the original `200` (their usual
        `response.json()` and status-code handling keeps working unchanged). A fresh `200`
        with an `ETag` header is stored for next time; any other status - including a `200`
        without an `ETag`, or an error - leaves the cache untouched.
        """
        fp = fingerprint(token)
        key = _cache_key(url, params)
        cached = self._cache.get(fp, key)
        headers = _headers(token)
        if cached is not None:
            headers["If-None-Match"] = cached[0]
        try:
            response = await self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(context) from exc
        if response.status_code == 304:
            if cached is None:
                # Cannot happen unless GitHub answers 304 to a request that carried no
                # If-None-Match; treat it as a transport failure rather than crash on `.json()`.
                raise GitHubUnavailable(f"{context}: unexpected 304 without a cached entry")
            return httpx.Response(304, json=cached[1], request=response.request)
        if response.status_code == 200:
            etag = response.headers.get("etag")
            if etag:
                self._cache.put(fp, key, etag=etag, body=response.json())
        return response

    async def list_repositories(self, token: str) -> RepositoryPage:
        log.debug(
            "conditional cache stats so far: hits=%d misses=%d stores=%d",
            self._cache.hits,
            self._cache.misses,
            self._cache.stores,
        )
        repositories: list[Repository] = []
        dependabot_by_repo: dict[str, tuple[SeverityCounts | None, int | None]] = {}
        release_by_repo: dict[str, ReleaseInfo | None] = {}
        rate_limit: RateLimit | None = None
        cursor: str | None = None
        while True:
            data = await self._graphql(
                token,
                _REPOSITORIES_QUERY,
                {
                    "cursor": cursor,
                    "pageSize": _PAGE_SIZE,
                    "prDetails": _PR_DETAIL_ITEMS,
                    "issueDetails": _ISSUE_DETAIL_ITEMS,
                },
            )
            rl = data.get("rateLimit")
            if rl:
                rate_limit = RateLimit(rl["remaining"], rl["limit"], _parse_dt(rl.get("resetAt")))
                if "cost" in rl:
                    log.debug("repositories query cost=%s", rl["cost"])
            connection = data["viewer"]["repositories"]
            for node in connection["nodes"]:
                if not node:
                    continue
                repo = _repository_from_node(node)
                repositories.append(repo)
                dependabot_by_repo[repo.full_name] = _dependabot_from_node(node)
                release_by_repo[repo.full_name] = _release_from_node(node.get("latestRelease"))
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            cursor = page["endCursor"]
        return RepositoryPage(tuple(repositories), rate_limit, dependabot_by_repo, release_by_repo)

    async def fetch_hygiene(self, token: str, repo_ids: Sequence[str]) -> HygienePage:
        """Hygiene facts and branch listings for `repo_ids`, in batches of 25.

        Each batch is retried once (after a short wait, split into two halves) on a
        transient failure; a half that still fails is simply absent from the result, which
        the use case treats as "hygiene not applicable / no branch data" for this refresh
        rather than failing the whole overview.
        """
        ids = list(repo_ids)
        batches = [
            ids[i : i + _HYGIENE_BATCH_SIZE] for i in range(0, len(ids), _HYGIENE_BATCH_SIZE)
        ]
        results = await asyncio.gather(*(self._fetch_hygiene_batch(token, b) for b in batches))
        hygiene_by_repo: dict[str, HygieneFacts] = {}
        branches_by_repo: dict[str, BranchListing] = {}
        for batch_hygiene, batch_branches in results:
            hygiene_by_repo.update(batch_hygiene)
            branches_by_repo.update(batch_branches)
        return HygienePage(hygiene_by_repo, branches_by_repo)

    async def _fetch_hygiene_batch(
        self, token: str, ids: list[str]
    ) -> tuple[dict[str, HygieneFacts], dict[str, BranchListing]]:
        if not ids:
            return {}, {}
        try:
            nodes = await self._hygiene_nodes_once(token, ids)
        except _RetryableHygieneError:
            await asyncio.sleep(1)
            return await self._fetch_hygiene_split(token, ids)
        return _hygiene_from_nodes(nodes)

    async def _fetch_hygiene_split(
        self, token: str, ids: list[str]
    ) -> tuple[dict[str, HygieneFacts], dict[str, BranchListing]]:
        """Retry a failed batch split into two halves, once each. A half that fails again is
        dropped: its repositories are simply absent from the result."""
        mid = max(1, len(ids) // 2)
        halves = [ids[:mid], ids[mid:]] if len(ids) > 1 else [ids]
        hygiene_by_repo: dict[str, HygieneFacts] = {}
        branches_by_repo: dict[str, BranchListing] = {}
        for half in halves:
            if not half:
                continue
            try:
                nodes = await self._hygiene_nodes_once(token, half)
            except _RetryableHygieneError:
                log.warning("hygiene batch degraded for %d repositories after retry", len(half))
                continue
            h, b = _hygiene_from_nodes(nodes)
            hygiene_by_repo.update(h)
            branches_by_repo.update(b)
        return hygiene_by_repo, branches_by_repo

    async def _hygiene_nodes_once(self, token: str, ids: list[str]) -> list[dict[str, Any]]:
        try:
            response = await self._client.post(
                f"{self._api_url}/graphql",
                json={
                    "query": _HYGIENE_QUERY,
                    "variables": {"ids": ids, "refsPageSize": _REFS_PAGE_SIZE},
                },
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("hygiene graphql request failed") from exc
        if response.status_code in _RETRYABLE_HTTP_STATUSES:
            raise _RetryableHygieneError(f"HTTP {response.status_code}")
        _raise_for_status(response, context="hygiene graphql")
        payload = response.json()
        errors = payload.get("errors")
        if errors:
            types = {e.get("type") for e in errors}
            if "RATE_LIMITED" in types:
                raise RateLimited("hygiene graphql")
            if _RESOURCE_LIMITS_EXCEEDED in types:
                raise _RetryableHygieneError(_RESOURCE_LIMITS_EXCEEDED)
            messages = "; ".join(e.get("message", "?") for e in errors)
            if payload.get("data") is None:
                raise GitHubUnavailable(f"hygiene graphql: {messages}")
            log.warning("hygiene graphql partial errors: %s", messages)
        data = payload.get("data") or {}
        return [n for n in (data.get("nodes") or []) if n]

    async def fetch_security(
        self, token: str, owner: str, name: str
    ) -> tuple[SeverityCounts | None, int | None]:
        code_scanning = await self._fetch_code_scanning(token, owner, name)
        secret_scanning = await self._fetch_secret_scanning(token, owner, name)
        return code_scanning, secret_scanning

    async def _fetch_code_scanning(
        self, token: str, owner: str, name: str
    ) -> SeverityCounts | None:
        context = f"code scanning {owner}/{name}"
        response = await self._conditional_get(
            token,
            f"{self._api_url}/repos/{owner}/{name}/code-scanning/alerts",
            params={"state": "open", "per_page": 100},
            context=context,
        )
        if _optional_or_raise(response, context=context):
            return None
        return _severity_counts_from_code_scanning_alerts(response.json())

    async def _fetch_secret_scanning(self, token: str, owner: str, name: str) -> int | None:
        context = f"secret scanning {owner}/{name}"
        response = await self._conditional_get(
            token,
            f"{self._api_url}/repos/{owner}/{name}/secret-scanning/alerts",
            params={"state": "open", "per_page": 100},
            context=context,
        )
        if _optional_or_raise(response, context=context):
            return None
        return len(response.json())

    async def count_commits_since(
        self, token: str, owner: str, name: str, base: str, head: str
    ) -> int | None:
        context = f"compare {owner}/{name}"
        response = await self._conditional_get(
            token, f"{self._api_url}/repos/{owner}/{name}/compare/{base}...{head}", context=context
        )
        if _optional_or_raise(response, context=context):
            return None
        return int(response.json()["ahead_by"])

    async def list_notifications(self, token: str) -> list[Notification] | None:
        response = await self._conditional_get(
            token,
            f"{self._api_url}/notifications",
            params={"per_page": 50},
            context="notifications",
        )
        if _optional_or_raise(response, context="notifications"):
            return None
        return [_notification_from_json(n) for n in response.json()]

    async def list_recent_runs(
        self, token: str, owner: str, name: str, limit: int
    ) -> list[WorkflowRun]:
        context = f"runs {owner}/{name}"
        response = await self._conditional_get(
            token,
            f"{self._api_url}/repos/{owner}/{name}/actions/runs",
            params={"per_page": limit},
            context=context,
        )
        if response.status_code == 404:
            raise ActionsUnavailable("disabled")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") != "0":
            raise ActionsUnavailable("forbidden")
        _raise_for_status(response, context=context)
        return [_run_from_json(r) for r in response.json().get("workflow_runs", [])]

    async def list_run_durations(
        self, token: str, owner: str, name: str, since: datetime, max_pages: int | None = None
    ) -> RepoUsage:
        """Wall-clock CI time of `owner/name`'s workflow runs created on/after `since`.

        Rides the same conditional-GET cache as every other REST call here, so a repository
        with no new runs since the last refresh answers `304` and costs nothing. Unlike
        `list_recent_runs`, an unavailable Actions tab (404/403) is not an error here - the
        caller just gets a zero `RepoUsage`, same as `fetch_actions_usage`.

        `max_pages` bounds the cost per repository; the caller spends more of it on private
        repositories, because only those consume the account's Actions quota and the figure is
        worthless as a lower bound there.
        """
        pages = _RUN_DURATIONS_MAX_PAGES if max_pages is None else max(1, max_pages)
        context = f"run durations {owner}/{name}"
        since_filter = f">={since.strftime('%Y-%m-%d')}"
        runs: list[dict[str, Any]] = []
        total_count = 0
        for page in range(1, pages + 1):
            params: dict[str, Any] = {"created": since_filter, "per_page": _RUN_DURATIONS_PAGE_SIZE}
            if page > 1:
                params["page"] = page
            response = await self._conditional_get(
                token,
                f"{self._api_url}/repos/{owner}/{name}/actions/runs",
                params=params,
                context=context,
            )
            if _optional_or_raise(response, context=context):
                if page == 1:
                    return RepoUsage(seconds=0, runs=0, truncated=False)
                break
            data = response.json()
            if page == 1:
                total_count = int(data.get("total_count", 0))
            runs.extend(data.get("workflow_runs", []))
            if total_count <= _RUN_DURATIONS_PAGE_SIZE:
                break

        seconds = sum(_run_from_json(r).duration_seconds for r in runs)
        truncated = total_count > _RUN_DURATIONS_PAGE_SIZE * pages
        return RepoUsage(seconds=seconds, runs=len(runs), truncated=truncated)

    async def list_failed_jobs(
        self, token: str, owner: str, name: str, run_id: int
    ) -> tuple[FailedJob, ...]:
        context = f"jobs {owner}/{name}#{run_id}"
        response = await self._conditional_get(
            token,
            f"{self._api_url}/repos/{owner}/{name}/actions/runs/{run_id}/jobs",
            params={"filter": "latest", "per_page": 50},
            context=context,
        )
        if _optional_or_raise(response, context=context):
            return ()
        jobs = response.json().get("jobs", [])
        return tuple(
            _failed_job_from_json(j) for j in jobs if j.get("conclusion") in _JOB_FAILED_CONCLUSIONS
        )

    async def rerun_failed_jobs(self, token: str, owner: str, name: str, run_id: int) -> None:
        """`POST .../rerun-failed-jobs`. Not a conditional GET: this is the app's one write."""
        context = f"rerun {owner}/{name}#{run_id}"
        try:
            response = await self._client.post(
                f"{self._api_url}/repos/{owner}/{name}/actions/runs/{run_id}/rerun-failed-jobs",
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(context) from exc
        status = response.status_code
        if status in (201, 202):
            return
        if status == 401:
            raise AuthenticationError(context)
        if status == 403:
            raise AccessDenied(context)
        if status == 404:
            raise ActionsUnavailable(context)
        if status == 409:
            raise RunNotRerunnable(context)
        raise GitHubUnavailable(f"{context}: HTTP {status}")

    async def list_repo_items(
        self, token: str, owner: str, name: str, kind: str, cursor: str | None, limit: int
    ) -> RepoItemPage:
        query = _ITEMS_QUERY_BY_KIND.get(kind)
        if query is None:
            raise ValueError(f"unknown item kind: {kind!r}")
        data = await self._graphql(
            token, query, {"owner": owner, "name": name, "cursor": cursor, "pageSize": limit}
        )
        field_name = "pullRequests" if kind == "pull_requests" else "issues"
        connection = (data.get("repository") or {}).get(field_name) or {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [],
        }
        page_info = connection["pageInfo"]
        next_cursor = page_info["endCursor"] if page_info["hasNextPage"] else None
        nodes = [n for n in connection["nodes"] if n]
        if kind == "pull_requests":
            return RepoItemPage(
                pull_requests=tuple(_pull_request_from_node(n) for n in nodes),
                next_cursor=next_cursor,
            )
        return RepoItemPage(
            issues=tuple(_issue_from_node(n) for n in nodes), next_cursor=next_cursor
        )

    async def fetch_actions_usage(self, token: str, login: str) -> ActionsUsage:
        context = f"actions usage {login}"
        response = await self._conditional_get(
            token, f"{self._api_url}/users/{login}/settings/billing/actions", context=context
        )
        if _optional_or_raise(response, context=context):
            return ActionsUsage(
                available=False, minutes_used=None, included_minutes=None, paid_minutes_used=None
            )
        data = response.json()
        return ActionsUsage(
            available=True,
            minutes_used=int(data.get("total_minutes_used", 0)),
            included_minutes=int(data.get("included_minutes", 0)),
            paid_minutes_used=int(data.get("total_paid_minutes_used", 0)),
        )

    async def search_inbox(self, token: str) -> Inbox:
        variables = {
            "reviewRequested": "is:open is:pr review-requested:@me archived:false",
            "changesRequested": "is:open is:pr author:@me review:changes-requested archived:false",
            "assigned": "is:open assignee:@me archived:false",
            "mentioned": "is:open mentions:@me -author:@me archived:false",
        }
        data = await self._graphql(token, _INBOX_QUERY, variables)
        buckets: dict[str, tuple[AttentionItem, ...]] = {}
        for field_name, kind in _INBOX_FIELDS:
            nodes = (data.get(field_name) or {}).get("nodes") or []
            items = []
            for node in nodes:
                if not node:
                    continue
                item = _attention_item_from_node(node, kind)
                if item is not None:
                    items.append(item)
            buckets[field_name] = tuple(items)
        return Inbox(
            review_requested=buckets["review_requested"],
            changes_requested=buckets["changes_requested"],
            assigned=buckets["assigned"],
            mentioned=buckets["mentioned"],
        )

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


def _pr_checks(node: dict[str, Any]) -> ChecksState | None:
    commit_nodes = (node.get("commits") or {}).get("nodes") or []
    if not commit_nodes:
        return None
    rollup = (commit_nodes[0].get("commit") or {}).get("statusCheckRollup")
    if not rollup:
        return None
    return _CHECKS_STATE_BY_ROLLUP.get(rollup.get("state"))


_SEVERITY_LEVELS = ("critical", "high", "moderate", "low")
_RULE_SEVERITY_FALLBACK = {"error": "high", "warning": "moderate", "note": "low"}


def _dependabot_from_node(node: dict[str, Any]) -> tuple[SeverityCounts | None, int | None]:
    """Dependabot severity counts from the repositories query's `vulnerabilityAlerts` field.

    `None` when the field itself is null: the feature is disabled for the repository, or a
    partial GraphQL error nulled it out (missing `security_events` scope, no permission)."""
    alerts = node.get("vulnerabilityAlerts")
    if alerts is None:
        return None, None
    counts = dict.fromkeys(_SEVERITY_LEVELS, 0)
    for alert_node in alerts.get("nodes") or []:
        severity = ((alert_node or {}).get("securityVulnerability") or {}).get("severity")
        level = (severity or "").lower()
        if level in counts:
            counts[level] += 1
    total = alerts.get("totalCount")
    return SeverityCounts(**counts), (int(total) if total is not None else None)


def _release_from_node(node: dict[str, Any] | None) -> ReleaseInfo | None:
    if not node:
        return None
    return ReleaseInfo(
        tag=node["tagName"],
        name=node.get("name"),
        published_at=_parse_dt(node.get("publishedAt")),
        url=node.get("url", ""),
        is_prerelease=bool(node.get("isPrerelease", False)),
        unreleased_commits=None,
    )


def _severity_counts_from_code_scanning_alerts(alerts: list[dict[str, Any]]) -> SeverityCounts:
    counts = dict.fromkeys(_SEVERITY_LEVELS, 0)
    for alert in alerts:
        rule = alert.get("rule") or {}
        level = rule.get("security_severity_level") or _RULE_SEVERITY_FALLBACK.get(
            rule.get("severity")
        )
        if level in counts:
            counts[level] += 1
    return SeverityCounts(**counts)


def _subject_html_url(subject: dict[str, Any], repo_html_url: str) -> str | None:
    """Convert a notification's API subject URL to the page a person can open.

    Issues and pull requests translate cleanly from the REST API URL; every other subject
    type (releases, discussions, check suites, ...) falls back to the repository page.
    """
    subject_type = subject.get("type")
    url = subject.get("url")
    if subject_type in ("Issue", "PullRequest") and url:
        html_url = url.replace("https://api.github.com/repos/", "https://github.com/")
        if subject_type == "PullRequest":
            html_url = html_url.replace("/pulls/", "/pull/")
        return html_url
    return repo_html_url or None


def _notification_from_json(data: dict[str, Any]) -> Notification:
    subject = data.get("subject") or {}
    repo = data.get("repository") or {}
    return Notification(
        id=str(data["id"]),
        reason=data.get("reason", ""),
        subject_title=subject.get("title", ""),
        subject_type=subject.get("type", ""),
        subject_url=_subject_html_url(subject, repo.get("html_url", "")),
        repo_full_name=repo.get("full_name", ""),
        updated_at=_parse_dt(data.get("updated_at")) or datetime.now(UTC),
        unread=bool(data.get("unread", True)),
    )


def _last_commit_from_node(target: dict[str, Any] | None) -> LastCommit | None:
    if not target or "oid" not in target:
        return None
    author = target.get("author") or {}
    user = author.get("user") or {}
    return LastCommit(
        sha=target["oid"],
        headline=target.get("messageHeadline", ""),
        author_login=user.get("login"),
        author_name=author.get("name"),
        committed_at=_parse_dt(target.get("committedDate")) or datetime.now(UTC),
        url=target.get("url", ""),
    )


def _branch_from_ref_node(node: dict[str, Any]) -> Branch:
    target = node.get("target") or {}
    author = target.get("author") or {}
    user = author.get("user") or {}
    return Branch(
        name=node["name"],
        last_commit_at=_parse_dt(target.get("committedDate")),
        author=user.get("login") or author.get("name"),
    )


def _branches_without_pr_from_node(
    node: dict[str, Any], *, default_branch: str | None
) -> tuple[Branch, ...]:
    """Branches (other than the default one) with no pull request, oldest commit first."""
    refs = node.get("refs") or {}
    branches = [
        _branch_from_ref_node(ref_node)
        for ref_node in refs.get("nodes") or []
        if ref_node
        and ref_node["name"] != default_branch
        and int((ref_node.get("associatedPullRequests") or {}).get("totalCount") or 0) == 0
    ]
    branches.sort(key=lambda b: (b.last_commit_at is None, b.last_commit_at))
    return tuple(branches)


def _hygiene_facts_from_node(node: dict[str, Any]) -> HygieneFacts:
    """Plain booleans/counts for `assess_hygiene`, read off one hygiene-query node.

    Archived repositories are never included in the hygiene query (the use case filters them
    out of the id list before batching), so `is_archived` is always False here.
    """
    workflow_entries = (node.get("workflowsDir") or {}).get("entries") or []
    workflow_file_count = sum(
        1 for e in workflow_entries if (e.get("name") or "").endswith(_WORKFLOW_FILE_SUFFIXES)
    )
    branch_protection_rules = node.get("branchProtectionRules") or {}
    # `rulesets` can come back nulled out by a partial GraphQL error for tokens/plans that
    # don't expose it; treat that the same as "no rulesets configured".
    rulesets = node.get("rulesets") or {}
    return HygieneFacts(
        has_readme=node.get("readmeFile") is not None,
        has_license=node.get("licenseInfo") is not None,
        workflow_file_count=workflow_file_count,
        has_dependabot_config=(
            node.get("dependabotYml") is not None or node.get("dependabotYaml") is not None
        ),
        has_renovate_config=(
            node.get("renovateJson") is not None
            or node.get("renovateJsonGithub") is not None
            or node.get("renovaterc") is not None
        ),
        branch_protection_rule_count=int(branch_protection_rules.get("totalCount") or 0),
        ruleset_count=int(rulesets.get("totalCount") or 0),
        vulnerability_alerts_enabled=bool(node.get("hasVulnerabilityAlertsEnabled", False)),
        delete_branch_on_merge=bool(node.get("deleteBranchOnMerge", False)),
        has_security_policy=(
            node.get("securityMd") is not None or node.get("securityMdGithub") is not None
        ),
        has_codeowners=(
            node.get("codeownersFile") is not None or node.get("codeownersFileGithub") is not None
        ),
        is_fork=bool(node.get("isFork", False)),
    )


def _hygiene_from_nodes(
    nodes: list[dict[str, Any]],
) -> tuple[dict[str, HygieneFacts], dict[str, BranchListing]]:
    """Map one hygiene-query response's repository nodes to facts and branch listings,
    both keyed by `nameWithOwner`."""
    hygiene_by_repo: dict[str, HygieneFacts] = {}
    branches_by_repo: dict[str, BranchListing] = {}
    for node in nodes:
        full_name = node.get("nameWithOwner")
        if not full_name:
            continue
        hygiene_by_repo[full_name] = _hygiene_facts_from_node(node)
        default_branch = (node.get("defaultBranchRef") or {}).get("name")
        refs = node.get("refs") or {}
        branches_by_repo[full_name] = BranchListing(
            branch_count=int(refs.get("totalCount") or 0),
            branches=_branches_without_pr_from_node(node, default_branch=default_branch),
        )
    return hygiene_by_repo, branches_by_repo


def _pull_request_from_node(p: dict[str, Any]) -> PullRequest:
    """Map one `pullRequests` GraphQL node. Shared by the repositories query and
    `list_repo_items`, which select the identical set of pull-request fields."""
    return PullRequest(
        number=p["number"],
        title=p["title"],
        url=p["url"],
        author=(p.get("author") or {}).get("login"),
        is_draft=bool(p["isDraft"]),
        updated_at=_parse_dt(p["updatedAt"]),  # type: ignore[arg-type]
        created_at=_parse_dt(p["createdAt"]),  # type: ignore[arg-type]
        head_branch=p.get("headRefName"),
        review_decision=(
            ReviewDecision(p["reviewDecision"].lower()) if p.get("reviewDecision") else None
        ),
        checks=_pr_checks(p),
        mergeable=(Mergeable(p["mergeable"].lower()) if p.get("mergeable") else Mergeable.UNKNOWN),
        is_bot=is_bot_login((p.get("author") or {}).get("login")),
    )


def _issue_from_node(i: dict[str, Any]) -> Issue:
    """Map one `issues` GraphQL node. Shared by the repositories query and `list_repo_items`."""
    return Issue(
        number=i["number"],
        title=i["title"],
        url=i["url"],
        author=(i.get("author") or {}).get("login"),
        updated_at=_parse_dt(i["updatedAt"]),  # type: ignore[arg-type]
        created_at=_parse_dt(i["createdAt"]),  # type: ignore[arg-type]
    )


def _repository_from_node(node: dict[str, Any]) -> Repository:
    language = node.get("primaryLanguage") or {}
    default_branch = node.get("defaultBranchRef") or {}
    prs = node["pullRequests"]
    issues = node["issues"]
    return Repository(
        full_name=node["nameWithOwner"],
        node_id=node["id"],
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
        pull_requests=tuple(_pull_request_from_node(p) for p in prs["nodes"] if p),
        issues=tuple(_issue_from_node(i) for i in issues["nodes"] if i),
        last_commit=_last_commit_from_node(default_branch.get("target")),
    )


def _attention_item_from_node(node: dict[str, Any], kind: AttentionKind) -> AttentionItem | None:
    repository = node.get("repository")
    updated_at = _parse_dt(node.get("updatedAt"))
    if not repository or updated_at is None:
        return None
    return AttentionItem(
        kind=kind,
        is_pull_request="isDraft" in node,
        repo_full_name=repository["nameWithOwner"],
        number=node["number"],
        title=node["title"],
        url=node["url"],
        author=(node.get("author") or {}).get("login"),
        updated_at=updated_at,
        is_draft=bool(node.get("isDraft", False)),
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
    created_at = _parse_dt(data.get("created_at")) or datetime.now(UTC)
    return WorkflowRun(
        id=int(data["id"]),
        workflow_name=data.get("name") or data.get("path") or "workflow",
        title=data.get("display_title") or data.get("name") or "",
        url=data.get("html_url", ""),
        branch=data.get("head_branch"),
        event=data.get("event", ""),
        status=_run_status(data.get("status"), data.get("conclusion")),
        run_number=int(data.get("run_number", 0)),
        created_at=created_at,
        updated_at=_parse_dt(data.get("updated_at")) or datetime.now(UTC),
        started_at=_parse_dt(data.get("run_started_at")) or created_at,
    )


def _first_failed_step(job: dict[str, Any]) -> str | None:
    """The name of the first step in `job` whose conclusion is a failure, if any."""
    for step in job.get("steps") or []:
        if step.get("conclusion") in _JOB_FAILED_CONCLUSIONS:
            return step.get("name")
    return None


def _failed_job_from_json(job: dict[str, Any]) -> FailedJob:
    return FailedJob(
        name=job.get("name", ""),
        step=_first_failed_step(job),
        url=job.get("html_url", ""),
    )
