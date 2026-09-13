"""Tests for `_refresh_active_sessions`, the extracted body of `app.main.refresh_loop` - the
loop itself just sleeps and calls this once per tick, so it is exercised here directly instead
of waiting on a real sleep."""

from datetime import UTC, datetime, timedelta

from app.application.errors import AuthenticationError, GitHubUnavailable, RateLimited
from app.infrastructure.settings import Settings
from app.main import _refresh_active_sessions
from app.web.container import Container
from tests.fakes import (
    FakeApi,
    FakeCache,
    FakeOAuth,
    FakeSessions,
    FakeUserState,
    PlainCipher,
    make_repo,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

SETTINGS_ENV = {
    "GITHUB_CLIENT_ID": "cid",
    "GITHUB_CLIENT_SECRET": "sec",
    "SECRET_KEY": "x" * 48,
    "BASE_URL": "http://testserver",
}


def build_container(api: FakeApi, cache: FakeCache | None = None) -> Container:
    settings = Settings.from_env(SETTINGS_ENV)
    return Container.assemble(
        settings,
        oauth=FakeOAuth(),
        api=api,
        sessions=FakeSessions(),
        cache=cache or FakeCache(),
        cipher=PlainCipher(),
        user_state=FakeUserState(),
    )


async def _create_session(container: Container) -> str:
    record = await container.complete_login("code")
    return record.id


async def test_refresh_active_sessions_refreshes_a_recently_active_session():
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    session_id = await _create_session(container)
    container.activity.touch(session_id, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 1


async def test_refresh_active_sessions_ignores_sessions_outside_the_idle_window():
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    session_id = await _create_session(container)
    container.activity.touch(session_id, 42, NOW - timedelta(hours=2))  # default idle: 30 min

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 0


async def test_refresh_active_sessions_forces_a_fresh_fetch_even_if_cached():
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    session_id = await _create_session(container)
    session = await container.resolve_session(session_id)
    await container.get_overview(session)  # warms the cache
    assert api.calls == 1
    container.activity.touch(session_id, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 2  # force_refresh=True bypassed the warm cache


async def test_refresh_active_sessions_forgets_session_on_authentication_error():
    api = FakeApi(repos=[make_repo("a")], list_repositories_error=AuthenticationError("revoked"))
    container = build_container(api)
    session_id = await _create_session(container)
    container.activity.touch(session_id, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert container.activity.active(NOW, timedelta(hours=1)) == []


async def test_refresh_active_sessions_forgets_a_session_that_no_longer_resolves():
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    container.activity.touch("ghost-session", 42, NOW)  # never created via complete_login

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 0
    assert container.activity.active(NOW, timedelta(hours=1)) == []


async def test_refresh_active_sessions_stops_the_cycle_on_rate_limited():
    api = FakeApi(repos=[make_repo("a")], list_repositories_error=RateLimited("x"))
    container = build_container(api)
    session_id_1 = await _create_session(container)
    session_id_2 = await _create_session(container)
    container.activity.touch(session_id_1, 42, NOW)
    container.activity.touch(session_id_2, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 1  # the second session is never attempted this cycle


async def test_refresh_active_sessions_continues_after_github_unavailable():
    api = FakeApi(repos=[make_repo("a")], list_repositories_error=GitHubUnavailable("down"))
    container = build_container(api)
    session_id_1 = await _create_session(container)
    session_id_2 = await _create_session(container)
    container.activity.touch(session_id_1, 42, NOW)
    container.activity.touch(session_id_2, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 2  # both sessions were attempted despite the first one's error


async def test_refresh_active_sessions_survives_an_unexpected_exception():
    api = FakeApi(repos=[make_repo("a")], list_repositories_error=RuntimeError("boom"))
    container = build_container(api)
    session_id_1 = await _create_session(container)
    session_id_2 = await _create_session(container)
    container.activity.touch(session_id_1, 42, NOW)
    container.activity.touch(session_id_2, 42, NOW)

    await _refresh_active_sessions(container, NOW)  # must not raise

    assert api.calls == 2


async def test_background_refresh_does_not_renew_activity():
    """Regression: the loop resolves sessions itself, and resolving used to count as activity.
    A session warmed once would then never leave the idle window, so it kept being refreshed
    for days after the user had closed the tab."""
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    session_id = await _create_session(container)
    container.activity.touch(session_id, 42, NOW)

    await _refresh_active_sessions(container, NOW + timedelta(minutes=5))

    assert api.calls == 1, "the session was inside the idle window, so it is refreshed"
    # Activity must still point at the original sighting, not at the background tick.
    assert container.activity.active(NOW + timedelta(minutes=45), timedelta(minutes=30)) == []


async def test_refresh_active_sessions_warms_each_user_only_once():
    """Two browsers of the same person are two sessions but share one cache entry, so a tick
    must not pay for the same refresh twice."""
    api = FakeApi(repos=[make_repo("a")])
    container = build_container(api)
    first = await _create_session(container)
    second = await _create_session(container)
    assert first != second
    container.activity.touch(first, 42, NOW)
    container.activity.touch(second, 42, NOW)

    await _refresh_active_sessions(container, NOW)

    assert api.calls == 1
