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
from app.domain.hygiene import HygieneFacts, RepoHygiene, assess_hygiene
from app.domain.models import (
    AttentionItem,
    AttentionKind,
    Branch,
    ChecksState,
    Inbox,
    Issue,
    LastCommit,
    Mergeable,
    Notification,
    PullRequest,
    RateLimit,
    ReleaseInfo,
    Repository,
    ReviewDecision,
    RunStatus,
    SeverityCounts,
    User,
    WorkflowRun,
)
from app.domain.pull_requests import is_bot_login

log = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
# Repository page size and refs-per-repository were both lowered from 50 to 25: at 50/50 the
# combined query (PRs, issues, vulnerability alerts, 11 file probes and 50 branch refs with
# their commit/PR lookups per repository) intermittently hit GitHub's per-query resource
# limits (RESOURCE_LIMITS_EXCEEDED partial errors, and once an outright 502) even though its
# reported `rateLimit.cost` stayed low (single digits to ~40). 25/25 measured a consistent
# cost of ~13 and 7-9s against a real 37-repository account with no partial errors.
_PAGE_SIZE = 25
_PR_DETAIL_ITEMS = 20
_ISSUE_DETAIL_ITEMS = 10
_REFS_PAGE_SIZE = 25
_WORKFLOW_FILE_SUFFIXES = (".yml", ".yaml")

_REPOSITORIES_QUERY = """
query Repositories(
  $cursor: String
  $pageSize: Int!
  $prDetails: Int!
  $issueDetails: Int!
  $refsPageSize: Int!
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
          nodes { number title url updatedAt author { login } }
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


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
    }


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
        dependabot_by_repo: dict[str, tuple[SeverityCounts | None, int | None]] = {}
        release_by_repo: dict[str, ReleaseInfo | None] = {}
        hygiene_by_repo: dict[str, RepoHygiene] = {}
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
                    "refsPageSize": _REFS_PAGE_SIZE,
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
                hygiene_by_repo[repo.full_name] = assess_hygiene(_hygiene_facts_from_node(node))
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                break
            cursor = page["endCursor"]
        return RepositoryPage(
            tuple(repositories), rate_limit, dependabot_by_repo, release_by_repo, hygiene_by_repo
        )

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
        try:
            response = await self._client.get(
                f"{self._api_url}/repos/{owner}/{name}/code-scanning/alerts",
                params={"state": "open", "per_page": 100},
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(context) from exc
        if _optional_or_raise(response, context=context):
            return None
        return _severity_counts_from_code_scanning_alerts(response.json())

    async def _fetch_secret_scanning(self, token: str, owner: str, name: str) -> int | None:
        context = f"secret scanning {owner}/{name}"
        try:
            response = await self._client.get(
                f"{self._api_url}/repos/{owner}/{name}/secret-scanning/alerts",
                params={"state": "open", "per_page": 100},
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(context) from exc
        if _optional_or_raise(response, context=context):
            return None
        return len(response.json())

    async def count_commits_since(
        self, token: str, owner: str, name: str, base: str, head: str
    ) -> int | None:
        context = f"compare {owner}/{name}"
        try:
            response = await self._client.get(
                f"{self._api_url}/repos/{owner}/{name}/compare/{base}...{head}",
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable(context) from exc
        if _optional_or_raise(response, context=context):
            return None
        return int(response.json()["ahead_by"])

    async def list_notifications(self, token: str) -> list[Notification] | None:
        try:
            response = await self._client.get(
                f"{self._api_url}/notifications",
                params={"per_page": 50},
                headers=_headers(token),
            )
        except httpx.HTTPError as exc:
            raise GitHubUnavailable("notifications") from exc
        if _optional_or_raise(response, context="notifications"):
            return None
        return [_notification_from_json(n) for n in response.json()]

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
    """Plain booleans/counts for `assess_hygiene`, read off the repositories query node."""
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
        is_archived=bool(node["isArchived"]),
        is_fork=bool(node["isFork"]),
    )


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
                created_at=_parse_dt(p["createdAt"]),  # type: ignore[arg-type]
                head_branch=p.get("headRefName"),
                review_decision=(
                    ReviewDecision(p["reviewDecision"].lower()) if p.get("reviewDecision") else None
                ),
                checks=_pr_checks(p),
                mergeable=(
                    Mergeable(p["mergeable"].lower()) if p.get("mergeable") else Mergeable.UNKNOWN
                ),
                is_bot=is_bot_login((p.get("author") or {}).get("login")),
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
        last_commit=_last_commit_from_node(default_branch.get("target")),
        branch_count=int((node.get("refs") or {}).get("totalCount") or 0),
        branches_without_pr=_branches_without_pr_from_node(
            node, default_branch=default_branch.get("name")
        ),
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
