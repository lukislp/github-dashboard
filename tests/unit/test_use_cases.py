from datetime import timedelta

import pytest

from app.application.errors import AccessDenied, AuthenticationError, GitHubUnavailable, RateLimited
from app.application.ports import BranchListing
from app.application.use_cases import (
    AccessPolicy,
    CompleteLogin,
    GetChanges,
    GetOverview,
    GetPreferences,
    Logout,
    MarkSeen,
    ResolveSession,
    SavePreferences,
    validate_preferences,
)
from app.domain.hygiene import RepoHygiene, assess_hygiene
from app.domain.models import (
    AttentionItem,
    AttentionKind,
    CiState,
    Inbox,
    Notification,
    Preferences,
    ReleaseInfo,
    RepoGroup,
    RunStatus,
    SeverityCounts,
)
from tests.fakes import (
    NOW,
    FakeApi,
    FakeCache,
    FakeOAuth,
    FakeSessions,
    FakeUserState,
    PlainCipher,
    make_branch,
    make_hygiene_facts,
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


async def test_get_overview_merges_graphql_dependabot_with_rest_security():
    api = FakeApi(
        repos=[make_repo("a")],
        dependabot_by_repo={"octocat/a": (SeverityCounts(1, 0, 0, 0), 1)},
        security={"octocat/a": (SeverityCounts(0, 1, 0, 0), 2)},
    )
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    security = result.overview.repos[0].security
    assert security.dependabot == SeverityCounts(1, 0, 0, 0)
    assert security.dependabot_total == 1
    assert security.code_scanning == SeverityCounts(0, 1, 0, 0)
    assert security.secret_scanning == 2
    assert api.security_calls == ["octocat/a"]


async def test_get_overview_skips_rest_security_when_disabled():
    api = FakeApi(
        repos=[make_repo("a")],
        dependabot_by_repo={"octocat/a": (SeverityCounts(1, 0, 0, 0), 1)},
        security={"octocat/a": (SeverityCounts(0, 1, 0, 0), 2)},
    )
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")
    uc = GetOverview(
        api=api,
        sessions=sessions,
        cipher=PlainCipher(),
        cache=cache,
        cache_ttl_seconds=60,
        runs_per_repo=5,
        max_concurrency=2,
        security_alerts=False,
        clock=clock,
    )

    result = await uc(record)

    security = result.overview.repos[0].security
    assert security.dependabot == SeverityCounts(1, 0, 0, 0)  # GraphQL part still runs
    assert security.code_scanning is None
    assert security.secret_scanning is None
    assert api.security_calls == []


async def test_get_overview_degrades_security_to_none_on_rate_limited_or_unavailable():
    api = FakeApi(repos=[make_repo("a")])
    api.security_error = RateLimited("code scanning")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    security = result.overview.repos[0].security
    assert security.code_scanning is None
    assert security.secret_scanning is None


async def test_get_overview_propagates_authentication_error_from_security():
    api = FakeApi(repos=[make_repo("a")])
    api.security_error = AuthenticationError("revoked")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(AuthenticationError):
        await build_overview_uc(api, sessions, cache)(record)
    assert sessions.records == {}


async def test_get_overview_skips_security_rest_for_archived_repos():
    api = FakeApi(
        repos=[make_repo("dead", archived=True)],
        dependabot_by_repo={"octocat/dead": (SeverityCounts(1, 0, 0, 0), 1)},
        security={"octocat/dead": (SeverityCounts(0, 1, 0, 0), 2)},
    )
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    security = result.overview.repos[0].security
    assert security.dependabot == SeverityCounts(1, 0, 0, 0)
    assert security.code_scanning is None
    assert api.security_calls == []


async def test_get_overview_fills_unreleased_commits_from_compare():
    release = ReleaseInfo(
        tag="v1.0.0",
        name="v1",
        published_at=NOW,
        url="u",
        is_prerelease=False,
        unreleased_commits=None,
    )
    api = FakeApi(
        repos=[make_repo("a")],
        release_by_repo={"octocat/a": release},
        commits_since={"octocat/a": 7},
    )
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    fetched_release = result.overview.repos[0].release
    assert fetched_release is not None
    assert fetched_release.unreleased_commits == 7
    assert api.commits_since_calls == ["octocat/a"]


async def test_get_overview_skips_compare_when_no_release():
    api = FakeApi(repos=[make_repo("a")])
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.repos[0].release is None
    assert api.commits_since_calls == []


async def test_get_overview_degrades_unreleased_commits_to_none_on_error():
    release = ReleaseInfo(
        tag="v1.0.0",
        name="v1",
        published_at=NOW,
        url="u",
        is_prerelease=False,
        unreleased_commits=None,
    )
    api = FakeApi(repos=[make_repo("a")], release_by_repo={"octocat/a": release})
    api.commits_since_error = GitHubUnavailable("compare down")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.repos[0].release.unreleased_commits is None


async def test_get_overview_includes_hygiene_from_api_by_default():
    facts = make_hygiene_facts(failing=("readme",))
    repo = make_repo("a")
    api = FakeApi(repos=[repo], hygiene_by_repo={"octocat/a": facts})
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.repos[0].hygiene == assess_hygiene(facts)
    assert api.hygiene_calls == [(repo.node_id,)]


async def test_get_overview_ignores_hygiene_when_disabled():
    facts = make_hygiene_facts(failing=("readme",))
    api = FakeApi(repos=[make_repo("a")], hygiene_by_repo={"octocat/a": facts})
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")
    uc = GetOverview(
        api=api,
        sessions=sessions,
        cipher=PlainCipher(),
        cache=cache,
        cache_ttl_seconds=60,
        runs_per_repo=5,
        max_concurrency=2,
        hygiene_checks=False,
        clock=clock,
    )

    result = await uc(record)

    assert result.overview.repos[0].hygiene == RepoHygiene((), applicable=False)
    assert api.hygiene_calls == []  # the hygiene query is skipped entirely


async def test_get_overview_excludes_archived_repos_from_hygiene_ids():
    live = make_repo("live")
    dead = make_repo("dead", archived=True)
    api = FakeApi(repos=[live, dead])
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    await build_overview_uc(api, sessions, cache)(record)

    assert api.hygiene_calls == [(live.node_id,)]


async def test_get_overview_attaches_branch_listing_from_hygiene_fetch():
    branch = make_branch("orphan")
    listing = BranchListing(branch_count=4, branches=(branch,))
    repo = make_repo("a")
    api = FakeApi(repos=[repo], branches_by_repo={"octocat/a": listing})
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    updated = result.overview.repos[0].repository
    assert updated.branch_count == 4
    assert updated.branches_without_pr == (branch,)


async def test_get_overview_hygiene_failure_leaves_overview_intact():
    api = FakeApi(repos=[make_repo("a", branch_count=0)])
    api.hygiene_error = GitHubUnavailable("hygiene down")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.repos[0].hygiene == RepoHygiene((), applicable=False)
    assert result.overview.repos[0].repository.branch_count == 0
    assert result.overview.repos[0].repository.branches_without_pr == ()


async def test_get_overview_hygiene_rate_limited_propagates():
    api = FakeApi(repos=[make_repo("a")])
    api.hygiene_error = RateLimited("hygiene")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(RateLimited):
        await build_overview_uc(api, sessions, cache)(record)


async def test_get_overview_hygiene_authentication_error_propagates():
    api = FakeApi(repos=[make_repo("a")])
    api.hygiene_error = AuthenticationError("revoked")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(AuthenticationError):
        await build_overview_uc(api, sessions, cache)(record)
    assert sessions.records == {}


async def test_get_overview_includes_notifications_when_available():
    notification = Notification(
        id="1",
        reason="mention",
        subject_title="t",
        subject_type="Issue",
        subject_url="u",
        repo_full_name="octocat/a",
        updated_at=NOW,
        unread=True,
    )
    api = FakeApi(repos=[make_repo("a")], notifications=[notification])
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.notifications == (notification,)
    assert result.overview.notifications_available is True
    assert api.notifications_calls == 1


async def test_get_overview_notifications_unavailable_when_scope_missing():
    api = FakeApi(repos=[make_repo("a")], notifications=None)
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.notifications == ()
    assert result.overview.notifications_available is False


async def test_get_overview_notifications_degrade_on_rate_limited():
    api = FakeApi(repos=[make_repo("a")])
    api.notifications_error = RateLimited("notifications")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    result = await build_overview_uc(api, sessions, cache)(record)

    assert result.overview.notifications == ()
    assert result.overview.notifications_available is False


async def test_get_overview_propagates_authentication_error_from_notifications():
    api = FakeApi(repos=[make_repo("a")])
    api.notifications_error = AuthenticationError("revoked")
    oauth, sessions, cache = FakeOAuth(), FakeSessions(), FakeCache()
    record = await build_login(oauth, sessions)("code")

    with pytest.raises(AuthenticationError):
        await build_overview_uc(api, sessions, cache)(record)
    assert sessions.records == {}


# -- preferences validation ---------------------------------------------------------------


def test_validate_preferences_accepts_within_limits():
    prefs = Preferences(
        groups=(RepoGroup("Backend", ("octocat/a", "octocat/b")),), favorites=("octocat/a",)
    )
    validate_preferences(prefs)  # does not raise


def test_validate_preferences_rejects_too_many_groups():
    prefs = Preferences(groups=tuple(RepoGroup(f"g{i}", ()) for i in range(31)))
    with pytest.raises(ValueError, match="30 groups"):
        validate_preferences(prefs)


def test_validate_preferences_rejects_empty_group_name():
    prefs = Preferences(groups=(RepoGroup("  ", ()),))
    with pytest.raises(ValueError, match="empty"):
        validate_preferences(prefs)


def test_validate_preferences_rejects_group_name_too_long():
    prefs = Preferences(groups=(RepoGroup("x" * 41, ()),))
    with pytest.raises(ValueError, match="too long"):
        validate_preferences(prefs)


def test_validate_preferences_rejects_case_insensitive_duplicate_group_name():
    prefs = Preferences(groups=(RepoGroup("Backend", ()), RepoGroup("backend", ())))
    with pytest.raises(ValueError, match="duplicate"):
        validate_preferences(prefs)


def test_validate_preferences_rejects_too_many_repos_total():
    group = RepoGroup("g", tuple(f"octocat/r{i}" for i in range(500)))
    prefs = Preferences(groups=(group,), favorites=("octocat/extra",))
    with pytest.raises(ValueError, match="500 repositories"):
        validate_preferences(prefs)


def test_validate_preferences_rejects_malformed_repo_name():
    prefs = Preferences(favorites=("not-a-repo-name",))
    with pytest.raises(ValueError, match="invalid repository name"):
        validate_preferences(prefs)


# -- per-user state use cases --------------------------------------------------------------


async def build_login_record():
    oauth, sessions = FakeOAuth(), FakeSessions()
    return await build_login(oauth, sessions)("code")


async def test_get_preferences_defaults_to_empty():
    record = await build_login_record()
    prefs = await GetPreferences(user_state=FakeUserState())(record)
    assert prefs == Preferences()


async def test_save_preferences_persists_and_get_preferences_returns_it():
    record = await build_login_record()
    user_state = FakeUserState()
    prefs = Preferences(groups=(RepoGroup("Backend", ("octocat/a",)),), favorites=("octocat/a",))

    await SavePreferences(user_state=user_state)(record, prefs)

    assert await GetPreferences(user_state=user_state)(record) == prefs


async def test_save_preferences_rejects_invalid_input_without_persisting():
    record = await build_login_record()
    user_state = FakeUserState()
    invalid = Preferences(favorites=("not-a-repo-name",))

    with pytest.raises(ValueError):
        await SavePreferences(user_state=user_state)(record, invalid)
    assert user_state.preferences == {}


async def test_mark_seen_stores_snapshot_of_overview():
    record = await build_login_record()
    api = FakeApi(repos=[make_repo("a", prs=1)])
    user_state = FakeUserState()
    result = await build_overview_uc(api, FakeSessions(), FakeCache())(record)

    snapshot = await MarkSeen(user_state=user_state, clock=lambda: NOW)(record, result.overview)

    assert snapshot.taken_at == NOW
    assert await user_state.get_snapshot(record.user.id) == snapshot


async def test_get_changes_returns_empty_before_first_mark_seen():
    record = await build_login_record()
    api = FakeApi(repos=[make_repo("a", prs=1)])
    user_state = FakeUserState()
    result = await build_overview_uc(api, FakeSessions(), FakeCache())(record)

    changes = await GetChanges(user_state=user_state)(record, result.overview)

    assert changes.since is None
    assert changes.total == 0


async def test_get_changes_reports_new_pr_after_mark_seen():
    record = await build_login_record()
    api = FakeApi(repos=[make_repo("a", prs=1)])
    user_state = FakeUserState()
    sessions, cache = FakeSessions(), FakeCache()
    uc = build_overview_uc(api, sessions, cache)

    seen_result = await uc(record)
    await MarkSeen(user_state=user_state, clock=lambda: NOW)(record, seen_result.overview)

    api.repos = [make_repo("a", prs=2)]
    later_result = await uc(record, force_refresh=True)
    changes = await GetChanges(user_state=user_state)(record, later_result.overview)

    assert changes.since == NOW
    assert changes.total == 1
    assert changes.new_prs[0].number == 2
