"""Tracks which sessions were recently active, so the background refresh only warms caches for
users who are actually looking.

In-memory on purpose: the warm cache this feeds is a nice-to-have, so losing the "who was
active" list on a restart is acceptable, and this avoids another schema migration. With
several replicas each one tracks (and warms) only the sessions it happens to see traffic for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(slots=True)
class ActivityTracker:
    """`session_id -> (user_id, last_seen)`, in memory for one process."""

    _last_seen: dict[str, tuple[int, datetime]] = field(default_factory=dict)

    def touch(self, session_id: str, user_id: int, now: datetime) -> None:
        """Record that `session_id` (belonging to `user_id`) was seen at `now`."""
        self._last_seen[session_id] = (user_id, now)

    def active(self, now: datetime, within: timedelta) -> list[tuple[str, int]]:
        """`(session_id, user_id)` pairs last seen within `within` of `now`."""
        cutoff = now - within
        return [
            (session_id, user_id)
            for session_id, (user_id, last_seen) in self._last_seen.items()
            if last_seen >= cutoff
        ]

    def forget(self, session_id: str) -> None:
        """Stop tracking `session_id` (e.g. it was deleted/expired). A no-op if unknown."""
        self._last_seen.pop(session_id, None)
