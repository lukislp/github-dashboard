from datetime import timedelta

import fakeredis.aioredis
import pytest

from app.application.errors import AuthenticationError
from app.application.ports import SessionRecord
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
