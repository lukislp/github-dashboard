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
        payload = json.dumps(self._to_dict(record))
        ttl = int((record.expires_at - record.created_at).total_seconds())
        await self._redis.set(self._key(record.id), payload, ex=max(ttl, 1))

    async def get(self, session_id: str) -> SessionRecord | None:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        return self._from_dict(json.loads(raw))

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def purge_expired(self, now: datetime) -> int:
        # Redis expires keys itself (see `ex=` in create).
        return 0

    async def update_tokens(
        self,
        session_id: str,
        *,
        token_ciphertext: str,
        token_expires_at: datetime | None,
        refresh_token_ciphertext: str | None,
        refresh_expires_at: datetime | None,
    ) -> None:
        key = self._key(session_id)
        raw = await self._redis.get(key)
        if raw is None:
            return
        data = json.loads(raw)
        data["token_ciphertext"] = token_ciphertext
        data["token_expires_at"] = token_expires_at.isoformat() if token_expires_at else None
        data["refresh_token_ciphertext"] = refresh_token_ciphertext
        data["refresh_expires_at"] = refresh_expires_at.isoformat() if refresh_expires_at else None
        # KEEPTTL: a refresh must not extend or reset the session's own lifetime.
        await self._redis.set(key, json.dumps(data), keepttl=True)

    @staticmethod
    def _to_dict(record: SessionRecord) -> dict:
        return {
            "id": record.id,
            "user": user_to_dict(record.user),
            "token_ciphertext": record.token_ciphertext,
            "created_at": record.created_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "token_expires_at": (
                record.token_expires_at.isoformat() if record.token_expires_at else None
            ),
            "refresh_token_ciphertext": record.refresh_token_ciphertext,
            "refresh_expires_at": (
                record.refresh_expires_at.isoformat() if record.refresh_expires_at else None
            ),
        }

    @staticmethod
    def _from_dict(data: dict) -> SessionRecord:
        return SessionRecord(
            id=data["id"],
            user=user_from_dict(data["user"]),
            token_ciphertext=data["token_ciphertext"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            token_expires_at=(
                datetime.fromisoformat(data["token_expires_at"])
                if data.get("token_expires_at")
                else None
            ),
            refresh_token_ciphertext=data.get("refresh_token_ciphertext"),
            refresh_expires_at=(
                datetime.fromisoformat(data["refresh_expires_at"])
                if data.get("refresh_expires_at")
                else None
            ),
        )


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
