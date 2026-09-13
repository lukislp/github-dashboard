"""Snapshotting an Overview and diffing a later one against it. Pure functions, no I/O.

`Overview` never stores a `Changes`: whether something is "new" depends on the viewer's own
Snapshot history, not on GitHub data. The web layer loads the stored Snapshot and calls
`diff_since` itself.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.models import AttentionItem, ChangedItem, Changes, Overview, Snapshot

_EMPTY_CHANGES = Changes(
    since=None,
    new_prs=(),
    new_issues=(),
    new_failed_runs=(),
    new_inbox=(),
    new_notifications=(),
    new_alert_repos=(),
)


def _item_key(repo_full_name: str, number: int) -> str:
    return f"{repo_full_name}#{number}"


def _inbox_key(kind: str, repo_full_name: str, number: int) -> str:
    return f"{kind}:{_item_key(repo_full_name, number)}"


def _inbox_items(overview: Overview) -> tuple[AttentionItem, ...]:
    return (
        overview.inbox.review_requested
        + overview.inbox.changes_requested
        + overview.inbox.assigned
        + overview.inbox.mentioned
    )


def snapshot_of(overview: Overview, now: datetime) -> Snapshot:
    """Capture the identity of everything currently in `overview`, to diff against later."""
    prs: set[str] = set()
    issues: set[str] = set()
    alert_repos: set[str] = set()
    for repo in overview.repos:
        full_name = repo.repository.full_name
        prs.update(_item_key(full_name, p.number) for p in repo.repository.pull_requests)
        issues.update(_item_key(full_name, i.number) for i in repo.repository.issues)
        if repo.security.total > 0:
            alert_repos.add(full_name)

    inbox = frozenset(
        _inbox_key(item.kind.value, item.repo_full_name, item.number)
        for item in _inbox_items(overview)
    )

    return Snapshot(
        taken_at=now,
        prs=frozenset(prs),
        issues=frozenset(issues),
        failed_runs=frozenset(f.run.id for f in overview.failures),
        inbox=inbox,
        notifications=frozenset(n.id for n in overview.notifications),
        alert_repos=frozenset(alert_repos),
    )


def diff_since(overview: Overview, snapshot: Snapshot | None) -> Changes:
    """What is new in `overview` since `snapshot` was taken.

    With no prior snapshot (first visit) there is nothing to compare against: everything
    is reported empty rather than "all new", per `_EMPTY_CHANGES`.
    """
    if snapshot is None:
        return _EMPTY_CHANGES

    new_prs: list[ChangedItem] = []
    new_issues: list[ChangedItem] = []
    new_alert_repos: list[str] = []
    for repo in overview.repos:
        full_name = repo.repository.full_name
        for pr in repo.repository.pull_requests:
            if _item_key(full_name, pr.number) not in snapshot.prs:
                new_prs.append(
                    ChangedItem(
                        full_name, pr.number, pr.title, pr.url, pr.author, pr.updated_at, True
                    )
                )
        for issue in repo.repository.issues:
            if _item_key(full_name, issue.number) not in snapshot.issues:
                new_issues.append(
                    ChangedItem(
                        full_name,
                        issue.number,
                        issue.title,
                        issue.url,
                        issue.author,
                        issue.updated_at,
                        False,
                    )
                )
        if repo.security.total > 0 and full_name not in snapshot.alert_repos:
            new_alert_repos.append(full_name)

    new_failed_runs = tuple(f for f in overview.failures if f.run.id not in snapshot.failed_runs)
    new_inbox = tuple(
        item
        for item in _inbox_items(overview)
        if _inbox_key(item.kind.value, item.repo_full_name, item.number) not in snapshot.inbox
    )
    new_notifications = tuple(
        n for n in overview.notifications if n.id not in snapshot.notifications
    )

    new_prs.sort(key=lambda c: c.updated_at, reverse=True)
    new_issues.sort(key=lambda c: c.updated_at, reverse=True)
    new_alert_repos.sort()

    return Changes(
        since=snapshot.taken_at,
        new_prs=tuple(new_prs),
        new_issues=tuple(new_issues),
        new_failed_runs=new_failed_runs,
        new_inbox=new_inbox,
        new_notifications=new_notifications,
        new_alert_repos=tuple(new_alert_repos),
    )
