from datetime import timedelta

import fakeredis.aioredis
import pytest

from app.application.ports import SessionRecord
from app.domain.models import Preferences, RepoGroup, Snapshot
from app.infrastructure.session_sqlite import SqliteSessionRepository
from app.infrastructure.user_state_redis import RedisUserStateRepository
from app.infrastructure.user_state_sqlite import SqliteUserStateRepository
from tests.fakes import NOW, VIEWER


@pytest.fixture(params=["sqlite", "redis"])
def user_state(request, tmp_path):
    if request.param == "sqlite":
        repo = SqliteUserStateRepository(str(tmp_path / "state.db"))
        yield repo
        repo.close()
    else:
        yield RedisUserStateRepository(fakeredis.aioredis.FakeRedis(decode_responses=True))


async def test_preferences_default_to_empty(user_state):
    assert await user_state.get_preferences(1) == Preferences()


async def test_preferences_roundtrip(user_state):
    prefs = Preferences(
        groups=(RepoGroup("Backend", ("octocat/a", "octocat/b")),), favorites=("octocat/a",)
    )
    await user_state.set_preferences(1, prefs)

    assert await user_state.get_preferences(1) == prefs
    # A different user is unaffected.
    assert await user_state.get_preferences(2) == Preferences()


async def test_snapshot_defaults_to_none_then_roundtrips(user_state):
    assert await user_state.get_snapshot(1) is None

    snapshot = Snapshot(
        taken_at=NOW,
        prs=frozenset({"octocat/a#1"}),
        issues=frozenset(),
        failed_runs=frozenset({1}),
        inbox=frozenset(),
        notifications=frozenset({"n1"}),
        alert_repos=frozenset({"octocat/a"}),
    )
    await user_state.set_snapshot(1, snapshot)

    assert await user_state.get_snapshot(1) == snapshot
    assert await user_state.get_snapshot(2) is None


async def test_setting_preferences_does_not_disturb_snapshot(user_state):
    snapshot = Snapshot(
        NOW, frozenset(), frozenset(), frozenset(), frozenset(), frozenset(), frozenset()
    )
    await user_state.set_snapshot(1, snapshot)
    await user_state.set_preferences(1, Preferences(favorites=("octocat/a",)))

    assert await user_state.get_snapshot(1) == snapshot
    assert await user_state.get_preferences(1) == Preferences(favorites=("octocat/a",))


async def test_sqlite_user_state_shares_db_file_with_sessions(tmp_path):
    db_path = str(tmp_path / "shared.db")
    sessions = SqliteSessionRepository(db_path)
    user_state = SqliteUserStateRepository(db_path)
    try:
        await sessions.create(
            SessionRecord(
                id="s1",
                user=VIEWER,
                token_ciphertext="c",
                created_at=NOW,
                expires_at=NOW + timedelta(hours=1),
            )
        )
        await user_state.set_preferences(VIEWER.id, Preferences(favorites=("octocat/a",)))

        assert await sessions.get("s1") is not None
        assert await user_state.get_preferences(VIEWER.id) == Preferences(favorites=("octocat/a",))
    finally:
        sessions.close()
        user_state.close()
