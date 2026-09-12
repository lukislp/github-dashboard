"""UserStateRepository backed by Redis. Keys never expire: preferences and the last-seen
Snapshot are keyed by GitHub user id and survive across logins."""

from __future__ import annotations

import json

from redis.asyncio import Redis

from app.domain.codec import (
    preferences_from_dict,
    preferences_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from app.domain.models import Preferences, Snapshot


class RedisUserStateRepository:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _prefs_key(self, user_id: int) -> str:
        return f"ghd:prefs:{user_id}"

    def _snapshot_key(self, user_id: int) -> str:
        return f"ghd:snapshot:{user_id}"

    async def get_preferences(self, user_id: int) -> Preferences:
        raw = await self._redis.get(self._prefs_key(user_id))
        return preferences_from_dict(json.loads(raw)) if raw else Preferences()

    async def set_preferences(self, user_id: int, prefs: Preferences) -> None:
        await self._redis.set(self._prefs_key(user_id), json.dumps(preferences_to_dict(prefs)))

    async def get_snapshot(self, user_id: int) -> Snapshot | None:
        raw = await self._redis.get(self._snapshot_key(user_id))
        return snapshot_from_dict(json.loads(raw)) if raw else None

    async def set_snapshot(self, user_id: int, snapshot: Snapshot) -> None:
        await self._redis.set(self._snapshot_key(user_id), json.dumps(snapshot_to_dict(snapshot)))
