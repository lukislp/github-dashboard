"""Serialisation of domain models to/from plain JSON-compatible dicts.

Used by the HTTP API and by cache/session adapters. Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.domain.models import (
    AttentionItem,
    AttentionKind,
    ChecksState,
    CiState,
    FailedRun,
    Inbox,
    Issue,
    LastCommit,
    Mergeable,
    Overview,
    PullRequest,
    RateLimit,
    RepoCi,
    RepoOverview,
    Repository,
    ReviewDecision,
    RunStatus,
    Totals,
    User,
    WorkflowRun,
)
from app.domain.pull_requests import pr_state


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _require_dt(value: str | None) -> datetime:
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError("missing datetime")
    return parsed


def user_to_dict(user: User) -> dict[str, Any]:
    return asdict(user)


def user_from_dict(data: dict[str, Any]) -> User:
    return User(**data)


def run_to_dict(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "workflow_name": run.workflow_name,
        "title": run.title,
        "url": run.url,
        "branch": run.branch,
        "event": run.event,
        "status": run.status.value,
        "failed": run.failed,
        "active": run.active,
        "run_number": run.run_number,
        "created_at": _dt(run.created_at),
        "updated_at": _dt(run.updated_at),
        "long_running": run.long_running,
    }


def run_from_dict(d: dict[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=d["id"],
        workflow_name=d["workflow_name"],
        title=d["title"],
        url=d["url"],
        branch=d.get("branch"),
        event=d["event"],
        status=RunStatus(d["status"]),
        run_number=d["run_number"],
        created_at=_require_dt(d["created_at"]),
        updated_at=_require_dt(d["updated_at"]),
        long_running=d.get("long_running", False),
    )


def pr_to_dict(pr: PullRequest) -> dict[str, Any]:
    return {
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "author": pr.author,
        "is_draft": pr.is_draft,
        "updated_at": _dt(pr.updated_at),
        "created_at": _dt(pr.created_at),
        "head_branch": pr.head_branch,
        "review_decision": pr.review_decision.value if pr.review_decision else None,
        "checks": pr.checks.value if pr.checks else None,
        "mergeable": pr.mergeable.value,
        "is_bot": pr.is_bot,
        "stale": pr.stale,
        "state": pr_state(pr).value,
    }


def pr_from_dict(d: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=d["number"],
        title=d["title"],
        url=d["url"],
        author=d.get("author"),
        is_draft=d["is_draft"],
        updated_at=_require_dt(d["updated_at"]),
        created_at=_require_dt(d["created_at"]),
        head_branch=d.get("head_branch"),
        review_decision=(
            ReviewDecision(d["review_decision"]) if d.get("review_decision") else None
        ),
        checks=ChecksState(d["checks"]) if d.get("checks") else None,
        mergeable=Mergeable(d["mergeable"]),
        is_bot=d["is_bot"],
        stale=d.get("stale", False),
    )


def issue_to_dict(issue: Issue) -> dict[str, Any]:
    return {
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "author": issue.author,
        "updated_at": _dt(issue.updated_at),
        "stale": issue.stale,
    }


def issue_from_dict(d: dict[str, Any]) -> Issue:
    return Issue(
        number=d["number"],
        title=d["title"],
        url=d["url"],
        author=d.get("author"),
        updated_at=_require_dt(d["updated_at"]),
        stale=d.get("stale", False),
    )


def last_commit_to_dict(commit: LastCommit | None) -> dict[str, Any] | None:
    if commit is None:
        return None
    return {
        "sha": commit.sha,
        "headline": commit.headline,
        "author_login": commit.author_login,
        "author_name": commit.author_name,
        "committed_at": _dt(commit.committed_at),
        "url": commit.url,
    }


def last_commit_from_dict(d: dict[str, Any] | None) -> LastCommit | None:
    if d is None:
        return None
    return LastCommit(
        sha=d["sha"],
        headline=d["headline"],
        author_login=d.get("author_login"),
        author_name=d.get("author_name"),
        committed_at=_require_dt(d["committed_at"]),
        url=d["url"],
    )


def repository_to_dict(repo: Repository) -> dict[str, Any]:
    return {
        "full_name": repo.full_name,
        "name": repo.name,
        "owner": repo.owner,
        "url": repo.url,
        "description": repo.description,
        "is_private": repo.is_private,
        "is_archived": repo.is_archived,
        "is_fork": repo.is_fork,
        "has_issues": repo.has_issues,
        "stars": repo.stars,
        "pushed_at": _dt(repo.pushed_at),
        "language": repo.language,
        "language_color": repo.language_color,
        "default_branch": repo.default_branch,
        "open_pr_count": repo.open_pr_count,
        "open_issue_count": repo.open_issue_count,
        "pull_requests": [pr_to_dict(p) for p in repo.pull_requests],
        "issues": [issue_to_dict(i) for i in repo.issues],
        "last_commit": last_commit_to_dict(repo.last_commit),
    }


def repository_from_dict(d: dict[str, Any]) -> Repository:
    return Repository(
        full_name=d["full_name"],
        name=d["name"],
        owner=d["owner"],
        url=d["url"],
        description=d.get("description"),
        is_private=d["is_private"],
        is_archived=d["is_archived"],
        is_fork=d["is_fork"],
        has_issues=d["has_issues"],
        stars=d["stars"],
        pushed_at=_parse_dt(d.get("pushed_at")),
        language=d.get("language"),
        language_color=d.get("language_color"),
        default_branch=d.get("default_branch"),
        open_pr_count=d["open_pr_count"],
        open_issue_count=d["open_issue_count"],
        pull_requests=tuple(pr_from_dict(p) for p in d.get("pull_requests", [])),
        issues=tuple(issue_from_dict(i) for i in d.get("issues", [])),
        last_commit=last_commit_from_dict(d.get("last_commit")),
    )


def ci_to_dict(ci: RepoCi) -> dict[str, Any]:
    return {
        "state": ci.state.value,
        "runs": [run_to_dict(r) for r in ci.runs],
        "failed_count": ci.failed_count,
        "active_count": ci.active_count,
        "error": ci.error,
        "long_running_count": ci.long_running_count,
    }


def ci_from_dict(d: dict[str, Any]) -> RepoCi:
    return RepoCi(
        state=CiState(d["state"]),
        runs=tuple(run_from_dict(r) for r in d.get("runs", [])),
        failed_count=d["failed_count"],
        active_count=d["active_count"],
        error=d.get("error"),
        long_running_count=d.get("long_running_count", 0),
    )


def attention_item_to_dict(item: AttentionItem) -> dict[str, Any]:
    return {
        "kind": item.kind.value,
        "is_pull_request": item.is_pull_request,
        "repo_full_name": item.repo_full_name,
        "number": item.number,
        "title": item.title,
        "url": item.url,
        "author": item.author,
        "updated_at": _dt(item.updated_at),
        "is_draft": item.is_draft,
    }


def attention_item_from_dict(d: dict[str, Any]) -> AttentionItem:
    return AttentionItem(
        kind=AttentionKind(d["kind"]),
        is_pull_request=d["is_pull_request"],
        repo_full_name=d["repo_full_name"],
        number=d["number"],
        title=d["title"],
        url=d["url"],
        author=d.get("author"),
        updated_at=_require_dt(d["updated_at"]),
        is_draft=d["is_draft"],
    )


def inbox_to_dict(inbox: Inbox) -> dict[str, Any]:
    return {
        "review_requested": [attention_item_to_dict(i) for i in inbox.review_requested],
        "changes_requested": [attention_item_to_dict(i) for i in inbox.changes_requested],
        "assigned": [attention_item_to_dict(i) for i in inbox.assigned],
        "mentioned": [attention_item_to_dict(i) for i in inbox.mentioned],
        "total": inbox.total,
    }


def inbox_from_dict(d: dict[str, Any]) -> Inbox:
    return Inbox(
        review_requested=tuple(attention_item_from_dict(i) for i in d.get("review_requested", [])),
        changes_requested=tuple(
            attention_item_from_dict(i) for i in d.get("changes_requested", [])
        ),
        assigned=tuple(attention_item_from_dict(i) for i in d.get("assigned", [])),
        mentioned=tuple(attention_item_from_dict(i) for i in d.get("mentioned", [])),
    )


def overview_to_dict(overview: Overview) -> dict[str, Any]:
    return {
        "viewer_login": overview.viewer_login,
        "generated_at": _dt(overview.generated_at),
        "totals": asdict(overview.totals),
        "repos": [
            {"repository": repository_to_dict(r.repository), "ci": ci_to_dict(r.ci)}
            for r in overview.repos
        ],
        "failures": [
            {"repo_full_name": f.repo_full_name, "run": run_to_dict(f.run)}
            for f in overview.failures
        ],
        "rate_limit": (
            {
                "remaining": overview.rate_limit.remaining,
                "limit": overview.rate_limit.limit,
                "reset_at": _dt(overview.rate_limit.reset_at),
            }
            if overview.rate_limit
            else None
        ),
        "inbox": inbox_to_dict(overview.inbox),
    }


def overview_from_dict(d: dict[str, Any]) -> Overview:
    rl = d.get("rate_limit")
    return Overview(
        viewer_login=d["viewer_login"],
        generated_at=_require_dt(d["generated_at"]),
        totals=Totals(**d["totals"]),
        repos=tuple(
            RepoOverview(repository_from_dict(r["repository"]), ci_from_dict(r["ci"]))
            for r in d["repos"]
        ),
        failures=tuple(
            FailedRun(f["repo_full_name"], run_from_dict(f["run"])) for f in d["failures"]
        ),
        rate_limit=(
            RateLimit(rl["remaining"], rl["limit"], _parse_dt(rl.get("reset_at"))) if rl else None
        ),
        inbox=inbox_from_dict(d.get("inbox", {})),
    )
