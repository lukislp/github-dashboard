"""In-process OverviewCache with TTL. Fine for one replica; use Redis for several."""

from __future__ import annotations

import time
from collections.abc import Callable

from app.domain.models import Overview

# How long a no-longer-fresh entry stays available to `get_stale` before it is dropped.
STALE_RETENTION_SECONDS = 7 * 24 * 3600


class MemoryOverviewCache:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        # user id -> (fresh until, drop after, overview), both instants on the monotonic clock
        self._entries: dict[int, tuple[float, float, Overview]] = {}
        self._monotonic = monotonic

    async def get(self, user_id: int) -> Overview | None:
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        fresh_until, _, overview = entry
        if fresh_until <= self._monotonic():
            return None
        return overview

    async def get_stale(self, user_id: int) -> Overview | None:
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        _, drop_after, overview = entry
        if drop_after <= self._monotonic():
            self._entries.pop(user_id, None)
            return None
        return overview

    async def set(self, user_id: int, overview: Overview, ttl_seconds: int) -> None:
        now = self._monotonic()
        self._entries[user_id] = (
            now + max(0, ttl_seconds),
            now + STALE_RETENTION_SECONDS,
            overview,
        )

    async def invalidate(self, user_id: int) -> None:
        self._entries.pop(user_id, None)
