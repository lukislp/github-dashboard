"""UserStateRepository backed by a single SQLite file: repo groups/favourites and the
viewer's last Snapshot, used to compute "changes since your last visit".

May share the session store's database file; it opens its own connection, same threading
pattern as `SqliteSessionRepository`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.domain.codec import (
    preferences_from_dict,
    preferences_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from app.domain.models import Preferences, Snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_state (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, kind)
);
"""

_PREFERENCES = "preferences"
_SNAPSHOT = "snapshot"


class SqliteUserStateRepository:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    async def get_preferences(self, user_id: int) -> Preferences:
        payload = await asyncio.to_thread(self._get, user_id, _PREFERENCES)
        return preferences_from_dict(json.loads(payload)) if payload is not None else Preferences()

    async def set_preferences(self, user_id: int, prefs: Preferences) -> None:
        await asyncio.to_thread(
            self._set, user_id, _PREFERENCES, json.dumps(preferences_to_dict(prefs))
        )

    async def get_snapshot(self, user_id: int) -> Snapshot | None:
        payload = await asyncio.to_thread(self._get, user_id, _SNAPSHOT)
        return snapshot_from_dict(json.loads(payload)) if payload is not None else None

    async def set_snapshot(self, user_id: int, snapshot: Snapshot) -> None:
        await asyncio.to_thread(
            self._set, user_id, _SNAPSHOT, json.dumps(snapshot_to_dict(snapshot))
        )

    # -- sync helpers, run in a worker thread -------------------------------------------

    def _get(self, user_id: int, kind: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM user_state WHERE user_id = ? AND kind = ?", (user_id, kind)
            ).fetchone()
        return row[0] if row else None

    def _set(self, user_id: int, kind: str, payload: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_state (user_id, kind, payload, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id, kind) DO UPDATE SET"
                " payload = excluded.payload, updated_at = excluded.updated_at",
                (user_id, kind, payload, datetime.now(UTC).isoformat()),
            )
