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
        try:
            runs = await api.list_recent_runs(token, candidate.owner, candidate.name, 5)
        except ActionsUnavailable:
            runs = []
        assert len(runs) <= 5
