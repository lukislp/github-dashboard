import json
import sqlite3
from datetime import timedelta

import fakeredis.aioredis
import pytest

from app.application.errors import AuthenticationError
from app.application.ports import SessionRecord
from app.domain.codec import user_to_dict
from app.domain.models import RunStatus
from app.domain.overview import build_overview, classify_ci
from app.infrastructure.cache_memory import MemoryOverviewCache
from app.infrastructure.session_redis import RedisOverviewCache, RedisSessionRepository
from app.infrastructure.session_sqlite import SqliteSessionRepository
from app.infrastructure.token_fernet import FernetTokenCipher
from tests.fakes import NOW, VIEWER, make_repo, make_run


def record(session_id: str = "s1", hours: int = 1) -> SessionRecord:
    return SessionRecord(
        id=session_id,
        user=VIEWER,
        token_ciphertext="cipher",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=hours),
    )


@pytest.fixture(params=["sqlite", "redis"])
async def sessions(request, tmp_path):
    if request.param == "sqlite":
        repo = SqliteSessionRepository(str(tmp_path / "s.db"))
        yield repo
        repo.close()
    else:
        yield RedisSessionRepository(fakeredis.aioredis.FakeRedis(decode_responses=True))


async def test_create_get_delete(sessions):
    await sessions.create(record())
    assert await sessions.get("s1") == record()
    await sessions.delete("s1")
    assert await sessions.get("s1") is None
    assert await sessions.get("missing") is None


async def test_sqlite_purge_expired(tmp_path):
    repo = SqliteSessionRepository(str(tmp_path / "s.db"))
    await repo.create(record("old", hours=1))
    await repo.create(record("fresh", hours=48))

    assert await repo.purge_expired(NOW + timedelta(hours=2)) == 1
    assert await repo.get("old") is None
    assert await repo.get("fresh") is not None
    repo.close()


async def test_update_tokens_persists_new_fields(sessions):
    await sessions.create(record())
    new_expires_at = NOW + timedelta(hours=8)
    new_refresh_expires_at = NOW + timedelta(days=180)

    await sessions.update_tokens(
        "s1",
        token_ciphertext="new-cipher",
        token_expires_at=new_expires_at,
        refresh_token_ciphertext="refresh-cipher",
        refresh_expires_at=new_refresh_expires_at,
    )

    updated = await sessions.get("s1")
    assert updated is not None
    assert updated.token_ciphertext == "new-cipher"
    assert updated.token_expires_at == new_expires_at
    assert updated.refresh_token_ciphertext == "refresh-cipher"
    assert updated.refresh_expires_at == new_refresh_expires_at
    # Everything else about the session is untouched.
    assert updated.id == "s1"
    assert updated.user == VIEWER
    assert updated.created_at == NOW


async def test_update_tokens_clears_refresh_fields_when_rotated_away(sessions):
    await sessions.create(record())
    await sessions.update_tokens(
        "s1",
        token_ciphertext="new-cipher",
        token_expires_at=None,
        refresh_token_ciphertext=None,
        refresh_expires_at=None,
    )

    updated = await sessions.get("s1")
    assert updated is not None
    assert updated.token_ciphertext == "new-cipher"
    assert updated.token_expires_at is None
    assert updated.refresh_token_ciphertext is None
    assert updated.refresh_expires_at is None


async def test_update_tokens_on_missing_session_is_a_noop(sessions):
    await sessions.update_tokens(
        "missing",
        token_ciphertext="x",
        token_expires_at=None,
        refresh_token_ciphertext=None,
        refresh_expires_at=None,
    )
    assert await sessions.get("missing") is None


async def test_redis_update_tokens_keeps_key_ttl():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    repo = RedisSessionRepository(redis)
    await repo.create(record())
    ttl_before = await redis.ttl("ghd:session:s1")
    assert ttl_before > 0

    await repo.update_tokens(
        "s1",
        token_ciphertext="new-cipher",
        token_expires_at=NOW + timedelta(hours=8),
        refresh_token_ciphertext="refresh-cipher",
        refresh_expires_at=NOW + timedelta(days=180),
    )

    ttl_after = await redis.ttl("ghd:session:s1")
    assert 0 < ttl_after <= ttl_before


async def test_sqlite_migrates_database_created_before_refresh_tokens(tmp_path):
    """A `sessions.db` written by a version of this app that predates refresh-token support
    has none of the three new columns. Opening it must add them (as NULL-able) rather than
    fail, and existing rows must keep working."""
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_json TEXT NOT NULL,
            token_ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, user_json, token_ciphertext, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            "old1",
            json.dumps(user_to_dict(VIEWER)),
            "old-cipher",
            NOW.isoformat(),
            (NOW + timedelta(hours=1)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()

    repo = SqliteSessionRepository(str(db_path))
    columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(sessions)")}
    assert {"token_expires_at", "refresh_token_ciphertext", "refresh_expires_at"} <= columns

    pre_existing = await repo.get("old1")
    assert pre_existing is not None
    assert pre_existing.token_ciphertext == "old-cipher"
    assert pre_existing.token_expires_at is None
    assert pre_existing.refresh_token_ciphertext is None
    assert pre_existing.refresh_expires_at is None

    # The migrated table also supports the new operation.
    await repo.update_tokens(
        "old1",
        token_ciphertext="new-cipher",
        token_expires_at=NOW + timedelta(hours=8),
        refresh_token_ciphertext="new-refresh",
        refresh_expires_at=NOW + timedelta(days=180),
    )
    updated = await repo.get("old1")
    assert updated is not None
    assert updated.token_ciphertext == "new-cipher"
    assert updated.refresh_token_ciphertext == "new-refresh"
    repo.close()


def test_sqlite_migration_is_idempotent(tmp_path):
    """Opening the same (already current) database twice must not fail re-adding columns."""
    db_path = str(tmp_path / "s.db")
    SqliteSessionRepository(db_path).close()
    repo = SqliteSessionRepository(db_path)  # second open: columns already exist
    columns = {row[1] for row in repo._conn.execute("PRAGMA table_info(sessions)")}
    assert {"token_expires_at", "refresh_token_ciphertext", "refresh_expires_at"} <= columns
    repo.close()


def sample_overview():
    return build_overview(
        viewer_login="octocat",
        repositories=[make_repo("a", prs=1)],
        ci_by_repo={"octocat/a": classify_ci([make_run(RunStatus.SUCCESS)])},
        rate_limit=None,
        now=NOW,
    )


async def test_memory_cache_expires():
    clock = {"t": 100.0}
    cache = MemoryOverviewCache(monotonic=lambda: clock["t"])
    await cache.set(1, sample_overview(), ttl_seconds=10)
    assert await cache.get(1) is not None
    clock["t"] = 111.0
    assert await cache.get(1) is None


async def test_redis_cache_roundtrip():
    cache = RedisOverviewCache(fakeredis.aioredis.FakeRedis(decode_responses=True))
    await cache.set(1, sample_overview(), ttl_seconds=30)
    assert await cache.get(1) == sample_overview()
    await cache.invalidate(1)
    assert await cache.get(1) is None


def test_fernet_cipher_roundtrip_and_key_rotation():
    cipher = FernetTokenCipher("a" * 40)
    ciphertext = cipher.encrypt("gho_secret")
    assert ciphertext != "gho_secret"
    assert cipher.decrypt(ciphertext) == "gho_secret"
    with pytest.raises(AuthenticationError):
        FernetTokenCipher("b" * 40).decrypt(ciphertext)
