"""Serialisation of domain models to/from plain JSON-compatible dicts.

Used by the HTTP API and by cache/session adapters. Pure functions, no I/O.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from app.domain.models import (
    CiState,
    FailedRun,
    Issue,
    Overview,
    PullRequest,
    RateLimit,
    RepoCi,
    RepoOverview,
    Repository,
    RunStatus,
    Totals,
    User,
    WorkflowRun,
)


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
        "pull_requests": [
            {
                "number": p.number,
                "title": p.title,
                "url": p.url,
                "author": p.author,
                "is_draft": p.is_draft,
                "updated_at": _dt(p.updated_at),
            }
            for p in repo.pull_requests
        ],
        "issues": [
            {
                "number": i.number,
                "title": i.title,
                "url": i.url,
                "author": i.author,
                "updated_at": _dt(i.updated_at),
            }
            for i in repo.issues
        ],
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
        pull_requests=tuple(
            PullRequest(
                number=p["number"],
                title=p["title"],
                url=p["url"],
                author=p.get("author"),
                is_draft=p["is_draft"],
                updated_at=_require_dt(p["updated_at"]),
            )
            for p in d.get("pull_requests", [])
        ),
        issues=tuple(
            Issue(
                number=i["number"],
                title=i["title"],
                url=i["url"],
                author=i.get("author"),
                updated_at=_require_dt(i["updated_at"]),
            )
            for i in d.get("issues", [])
        ),
    )


def ci_to_dict(ci: RepoCi) -> dict[str, Any]:
    return {
        "state": ci.state.value,
        "runs": [run_to_dict(r) for r in ci.runs],
        "failed_count": ci.failed_count,
        "active_count": ci.active_count,
        "error": ci.error,
    }


def ci_from_dict(d: dict[str, Any]) -> RepoCi:
    return RepoCi(
        state=CiState(d["state"]),
        runs=tuple(run_from_dict(r) for r in d.get("runs", [])),
        failed_count=d["failed_count"],
        active_count=d["active_count"],
        error=d.get("error"),
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
    )
