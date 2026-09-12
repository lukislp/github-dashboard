"""SessionRepository and OverviewCache backed by Redis. Use for more than one replica."""

from __future__ import annotations

import json
from datetime import datetime

from redis.asyncio import Redis

from app.application.ports import SessionRecord
from app.domain.codec import overview_from_dict, overview_to_dict, user_from_dict, user_to_dict
from app.domain.models import Overview


class RedisSessionRepository:
    def __init__(self, redis: Redis, prefix: str = "ghd:session:") -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    async def create(self, record: SessionRecord) -> None:
        payload = json.dumps(
            {
                "id": record.id,
                "user": user_to_dict(record.user),
                "token_ciphertext": record.token_ciphertext,
                "created_at": record.created_at.isoformat(),
                "expires_at": record.expires_at.isoformat(),
            }
        )
        ttl = int((record.expires_at - record.created_at).total_seconds())
        await self._redis.set(self._key(record.id), payload, ex=max(ttl, 1))

    async def get(self, session_id: str) -> SessionRecord | None:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        data = json.loads(raw)
        return SessionRecord(
            id=data["id"],
            user=user_from_dict(data["user"]),
            token_ciphertext=data["token_ciphertext"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
        )

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def purge_expired(self, now: datetime) -> int:
        # Redis expires keys itself (see `ex=` in create).
        return 0


class RedisOverviewCache:
    def __init__(self, redis: Redis, prefix: str = "ghd:overview:") -> None:
        self._redis = redis
        self._prefix = prefix

    def _key(self, user_id: int) -> str:
        return f"{self._prefix}{user_id}"

    async def get(self, user_id: int) -> Overview | None:
        raw = await self._redis.get(self._key(user_id))
        return overview_from_dict(json.loads(raw)) if raw else None

    async def set(self, user_id: int, overview: Overview, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        await self._redis.set(
            self._key(user_id), json.dumps(overview_to_dict(overview)), ex=ttl_seconds
        )

    async def invalidate(self, user_id: int) -> None:
        await self._redis.delete(self._key(user_id))
