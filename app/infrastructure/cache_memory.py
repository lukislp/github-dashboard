"""In-process OverviewCache with TTL. Fine for one replica; use Redis for several."""

from __future__ import annotations

import time
from collections.abc import Callable

from app.domain.models import Overview


class MemoryOverviewCache:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._entries: dict[int, tuple[float, Overview]] = {}
        self._monotonic = monotonic

    async def get(self, user_id: int) -> Overview | None:
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        expires_at, overview = entry
        if expires_at <= self._monotonic():
            self._entries.pop(user_id, None)
            return None
        return overview

    async def set(self, user_id: int, overview: Overview, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            self._entries.pop(user_id, None)
            return
        self._entries[user_id] = (self._monotonic() + ttl_seconds, overview)

    async def invalidate(self, user_id: int) -> None:
        self._entries.pop(user_id, None)
