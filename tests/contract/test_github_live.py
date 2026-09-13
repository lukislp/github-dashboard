"""Contract tests against the real GitHub API. Run with GH_TOKEN set; skipped otherwise."""

import os
import time

import httpx
import pytest

from app.application.errors import ActionsUnavailable
from app.domain.hygiene import assess_hygiene
from app.infrastructure.github_http import GitHubHttpApi

pytestmark = pytest.mark.skipif(not os.environ.get("GH_TOKEN"), reason="GH_TOKEN not set")


async def test_live_repositories_and_runs():
    token = os.environ["GH_TOKEN"]
    async with httpx.AsyncClient(timeout=30) as client:
        api = GitHubHttpApi(client, api_url="https://api.github.com")

        start = time.monotonic()
        page = await api.list_repositories(token)
        repositories_elapsed = time.monotonic() - start
        print(
            f"\nlist_repositories: {repositories_elapsed:.2f}s for {len(page.repositories)} repos"
        )

        assert page.repositories, "token sees no repositories"
        assert page.rate_limit is not None

        candidate = next(r for r in page.repositories if not r.is_archived)

        non_archived_ids = [r.node_id for r in page.repositories if not r.is_archived]
        start = time.monotonic()
        hygiene_page = await api.fetch_hygiene(token, non_archived_ids)
        hygiene_elapsed = time.monotonic() - start
        print(
            f"fetch_hygiene: {hygiene_elapsed:.2f}s for {len(non_archived_ids)} repos "
            f"in {max(1, -(-len(non_archived_ids) // 25))} batch(es)"
        )

        # Hygiene facts are computed for every non-archived repository requested; a non-fork
        # one always has all nine checks evaluated and is marked applicable.
        applicable_candidate = next(
            r for r in page.repositories if not r.is_archived and not r.is_fork
        )
        facts = hygiene_page.hygiene_by_repo.get(applicable_candidate.full_name)
        assert facts is not None, "hygiene batch for this repository was not recovered"
        hygiene = assess_hygiene(facts)
        assert hygiene.applicable is True
        assert hygiene.total == 9
        assert 0 <= hygiene.score <= 100

        # Branches without a pull request: sanity-check the shape only, since the actual
        # counts depend on the state of whatever repositories the token can see, and a
        # repository's listing may be absent if its hygiene batch degraded.
        listing = hygiene_page.branches_by_repo.get(candidate.full_name)
        if listing is not None:
            assert listing.branch_count >= 0
            for branch in listing.branches:
                assert branch.name != candidate.default_branch

        try:
            runs = await api.list_recent_runs(token, candidate.owner, candidate.name, 5)
        except ActionsUnavailable:
            runs = []
        assert len(runs) <= 5

        inbox = await api.search_inbox(token)
        assert inbox.total >= 0

        # Security and notifications scopes may or may not be granted on the local `gh` token;
        # the point of this test is that these calls never raise for a merely missing scope
        # (they degrade to `None`), not that the data is actually available.
        code_scanning, secret_scanning = await api.fetch_security(
            token, candidate.owner, candidate.name
        )
        assert code_scanning is None or code_scanning.total >= 0
        assert secret_scanning is None or secret_scanning >= 0

        release_candidate = page.release_by_repo.get(candidate.full_name)
        if release_candidate is not None and candidate.default_branch:
            commits = await api.count_commits_since(
                token,
                candidate.owner,
                candidate.name,
                release_candidate.tag,
                candidate.default_branch,
            )
            assert commits is None or commits >= 0

        notifications = await api.list_notifications(token)
        assert notifications is None or isinstance(notifications, list)

        # Failed jobs of one run, if any of the sampled runs failed. Never raises for a
        # merely-unavailable lookup (404/403 degrade to an empty tuple in the adapter itself).
        failed_run = next((r for r in runs if r.failed), None)
        if failed_run is not None:
            jobs = await api.list_failed_jobs(token, candidate.owner, candidate.name, failed_run.id)
            assert isinstance(jobs, tuple)

        # Actions billing usage: may be unavailable if the local `gh` token's scopes don't
        # include `user`, which must never surface as an error.
        viewer = await client.get(
            "https://api.github.com/user", headers={"Authorization": f"Bearer {token}"}
        )
        viewer_login = viewer.json()["login"]
        usage = await api.fetch_actions_usage(token, viewer_login)
        assert isinstance(usage.available, bool)
        if usage.available:
            assert usage.minutes_used is not None and usage.minutes_used >= 0

        # One page of the candidate repository's open pull requests, cursor-paginated.
        items_page = await api.list_repo_items(
            token, candidate.owner, candidate.name, "pull_requests", None, 50
        )
        print(
            f"\nlist_repo_items: {len(items_page.pull_requests)} pull request(s) on "
            f"{candidate.full_name} (next_cursor={items_page.next_cursor!r})"
        )
