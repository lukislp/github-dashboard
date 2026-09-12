"""Contract tests against the real GitHub API. Run with GH_TOKEN set; skipped otherwise."""

import os

import httpx
import pytest

from app.application.errors import ActionsUnavailable
from app.infrastructure.github_http import GitHubHttpApi

pytestmark = pytest.mark.skipif(not os.environ.get("GH_TOKEN"), reason="GH_TOKEN not set")


async def test_live_repositories_and_runs():
    token = os.environ["GH_TOKEN"]
    async with httpx.AsyncClient(timeout=30) as client:
        api = GitHubHttpApi(client, api_url="https://api.github.com")
        page = await api.list_repositories(token)
        assert page.repositories, "token sees no repositories"
        assert page.rate_limit is not None

        candidate = next(r for r in page.repositories if not r.is_archived)

        # Hygiene facts are always computed by the adapter; a non-archived, non-fork
        # repository always has all nine checks evaluated and is marked applicable.
        applicable_candidate = next(
            r for r in page.repositories if not r.is_archived and not r.is_fork
        )
        hygiene = page.hygiene_by_repo[applicable_candidate.full_name]
        assert hygiene.applicable is True
        assert hygiene.total == 9
        assert 0 <= hygiene.score <= 100

        # Branches without a pull request: sanity-check the shape only, since the actual
        # counts depend on the state of whatever repositories the token can see.
        assert candidate.branch_count >= 0
        for branch in candidate.branches_without_pr:
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
