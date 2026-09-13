"""SessionRepository backed by a single SQLite file. Suitable for one replica with a volume."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from app.application.ports import SessionRecord
from app.domain.codec import user_from_dict, user_to_dict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_json TEXT NOT NULL,
    token_ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires_at ON sessions (expires_at);
"""

# Columns added for refresh-token support, applied to a database created by an older version
# of this app. Each is nullable so an existing row (no expiry, no refresh token) stays valid.
_NEW_COLUMNS = (
    ("token_expires_at", "TEXT"),
    ("refresh_token_ciphertext", "TEXT"),
    ("refresh_expires_at", "TEXT"),
)


class SqliteSessionRepository:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._lock = threading.Lock()

    def _migrate(self) -> None:
        """Idempotent migration: add the refresh-token columns if a pre-existing database
        (created before this feature) does not have them yet."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        for name, sql_type in _NEW_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {sql_type}")

    def close(self) -> None:
        self._conn.close()

    async def create(self, record: SessionRecord) -> None:
        await asyncio.to_thread(self._create, record)

    async def get(self, session_id: str) -> SessionRecord | None:
        return await asyncio.to_thread(self._get, session_id)

    async def delete(self, session_id: str) -> None:
        await asyncio.to_thread(self._execute, "DELETE FROM sessions WHERE id = ?", (session_id,))

    async def purge_expired(self, now: datetime) -> int:
        return await asyncio.to_thread(
            self._execute, "DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),)
        )

    async def update_tokens(
        self,
        session_id: str,
        *,
        token_ciphertext: str,
        token_expires_at: datetime | None,
        refresh_token_ciphertext: str | None,
        refresh_expires_at: datetime | None,
    ) -> None:
        await asyncio.to_thread(
            self._execute,
            "UPDATE sessions SET token_ciphertext = ?, token_expires_at = ?,"
            " refresh_token_ciphertext = ?, refresh_expires_at = ? WHERE id = ?",
            (
                token_ciphertext,
                token_expires_at.isoformat() if token_expires_at else None,
                refresh_token_ciphertext,
                refresh_expires_at.isoformat() if refresh_expires_at else None,
                session_id,
            ),
        )

    # -- sync helpers, run in a worker thread -------------------------------------------

    def _execute(self, sql: str, params: tuple) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return cursor.rowcount

    def _create(self, record: SessionRecord) -> None:
        self._execute(
            "INSERT INTO sessions (id, user_json, token_ciphertext, created_at, expires_at,"
            " token_expires_at, refresh_token_ciphertext, refresh_expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id,
                json.dumps(user_to_dict(record.user)),
                record.token_ciphertext,
                record.created_at.isoformat(),
                record.expires_at.isoformat(),
                record.token_expires_at.isoformat() if record.token_expires_at else None,
                record.refresh_token_ciphertext,
                record.refresh_expires_at.isoformat() if record.refresh_expires_at else None,
            ),
        )

    def _get(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, user_json, token_ciphertext, created_at, expires_at,"
                " token_expires_at, refresh_token_ciphertext, refresh_expires_at"
                " FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            id=row[0],
            user=user_from_dict(json.loads(row[1])),
            token_ciphertext=row[2],
            created_at=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]),
            token_expires_at=datetime.fromisoformat(row[5]) if row[5] else None,
            refresh_token_ciphertext=row[6],
            refresh_expires_at=datetime.fromisoformat(row[7]) if row[7] else None,
        )
