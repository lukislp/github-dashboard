from datetime import timedelta

import pytest

from app.application.errors import AccessDenied, AuthenticationError, GitHubUnavailable, RateLimited
from app.application.use_cases import (
    AccessPolicy,
    CompleteLogin,
    GetOverview,
    Logout,
    ResolveSession,
)
from app.domain.models import AttentionItem, AttentionKind, CiState, Inbox, RunStatus
from tests.fakes import (
    NOW,
    FakeApi,
    FakeCache,
    FakeOAuth,
    FakeSessions,
    PlainCipher,
    make_repo,
    make_run,
)


def clock():
    return NOW


def build_login(oauth: FakeOAuth, sessions: FakeSessions, allowed: set[str] | None = None):
    return CompleteLogin(
        oauth=oauth,
        sessions=sessions,
        cipher=PlainCipher(),
        policy=AccessPolicy(frozenset(allowed or ())),
        session_ttl=timedelta(hours=2),
        clock=clock,
    )


async def test_complete_login_creates_encrypted_session():
    oauth, sessions = FakeOAuth(), FakeSessions()
    record = await build_login(oauth, sessions)("code-1")

    assert oauth.exchanged == ["code-1"]
    assert record.user.login == "octocat"
    assert record.token_ciphertext == "enc:gho_test"
    assert record.expires_at == NOW + timedelta(hours=2)
    assert sessions.records[record.id] == record


async def test_complete_login_rejects_and_revokes_when_not_allowed():
    oauth, sessions = FakeOAuth(), FakeSessions()
    with pytest.raises(AccessDenied):
        await build_login(oauth, sessions, allowed={"someone-else"})("code")
    assert oauth.revoked == ["gho_test"]
    assert sessions.records == {}


async def test_access_policy_is_case_insensitive():
    assert AccessPolicy(frozenset({"octocat"})).allows("OctoCat")
    assert AccessPolicy().allows("anyone")


async def test_resolve_session_drops_expired():
    oauth, sessions = FakeOAuth(), FakeSessions()
    record = await build_login(oauth, sessions)("code")
    resolve = ResolveSession(sessions=sessions, clock=lambda: NOW + timedelta(hours=3))

    assert await resolve(record.id) is None
    assert record.id not in sessions.records
    assert await resolve(None) is None


async def test_logout_deletes_session_and_revokes_token():
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")
    await Logout(oauth=oauth, sessions=sessions, cipher=PlainCipher(), cache=cache)(record)

    assert sessions.records == {}
    assert oauth.revoked == ["gho_test"]


def build_overview_uc(api: FakeApi, sessions: FakeSessions, cache: FakeCache, ttl: int = 60):
    return GetOverview(
        api=api,
        sessions=sessions,
        cipher=PlainCipher(),
        cache=cache,
        cache_ttl_seconds=ttl,
        runs_per_repo=5,
        max_concurrency=2,
        clock=clock,
    )


async def test_get_overview_skips_archived_and_marks_unavailable():
    api = FakeApi(
        repos=[make_repo("live"), make_repo("dead", archived=True), make_repo("noci")],
        runs={"octocat/live": [make_run(RunStatus.FAILURE)]},
    )
    api.unavailable.add("octocat/noci")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    by_name = {r.repository.name: r.ci for r in result.overview.repos}
    assert by_name["live"].state == CiState.FAILING
    assert by_name["dead"].state == CiState.SKIPPED
    assert by_name["noci"].state == CiState.UNAVAILABLE
    assert sorted(api.run_calls) == ["octocat/live", "octocat/noci"]
    assert result.from_cache is False
    assert result.overview.rate_limit is not None


async def test_get_overview_serves_from_cache_until_forced():
    api = FakeApi(repos=[make_repo("a")])
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")
    uc = build_overview_uc(api, sessions, cache)

    first = await uc(record)
    second = await uc(record)
    third = await uc(record, force_refresh=True)

    assert (first.from_cache, second.from_cache, third.from_cache) == (False, True, False)
    assert api.calls == 2


async def test_get_overview_revoked_token_deletes_session():
    api = FakeApi(repos=[make_repo("a")])
    api.token_valid = False
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(AuthenticationError):
        await build_overview_uc(api, sessions, cache)(record)
    assert sessions.records == {}


def _attention_item(number: int) -> AttentionItem:
    return AttentionItem(
        kind=AttentionKind.REVIEW_REQUESTED,
        is_pull_request=True,
        repo_full_name="octocat/a",
        number=number,
        title="t",
        url="u",
        author="bob",
        updated_at=NOW,
        is_draft=False,
    )


async def test_get_overview_includes_inbox_from_api():
    inbox = Inbox(
        review_requested=(_attention_item(1),), changes_requested=(), assigned=(), mentioned=()
    )
    api = FakeApi(repos=[make_repo("a")], inbox=inbox)
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.inbox.total == 1
    assert api.inbox_calls == 1


async def test_get_overview_falls_back_to_empty_inbox_on_rate_limited():
    api = FakeApi(repos=[make_repo("a")])
    api.inbox_error = RateLimited("inbox")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.inbox.total == 0


async def test_get_overview_falls_back_to_empty_inbox_on_github_unavailable():
    api = FakeApi(repos=[make_repo("a")])
    api.inbox_error = GitHubUnavailable("down")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.inbox.total == 0


async def test_get_overview_propagates_authentication_error_from_inbox():
    api = FakeApi(repos=[make_repo("a")])
    api.inbox_error = AuthenticationError("revoked")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(AuthenticationError):
        await build_overview_uc(api, sessions, cache)(record)
    assert sessions.records == {}
