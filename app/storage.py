"""SQLite event history.

One table, parameterized SQL, and a lock because the inference thread
writes while Flask reads.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.rules import Event


class EventStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        zone TEXT NOT NULL,
                        type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        message TEXT NOT NULL,
                        count INTEGER NOT NULL,
                        snapshot TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC)"
                )
        finally:
            connection.close()

    def add(self, event: Event, snapshot: str | None = None) -> int:
        connection = self._connect()
        try:
            with self._lock, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO events (ts, zone, type, severity, message, count, snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.ts,
                        event.zone,
                        event.type,
                        event.severity,
                        event.message,
                        event.count,
                        snapshot,
                    ),
                )
                return int(cursor.lastrowid)
        finally:
            connection.close()

    def recent(self, limit: int = 50) -> list[dict]:
        connection = self._connect()
        try:
            with self._lock:
                rows = connection.execute(
                    """
                    SELECT id, ts, zone, type, severity, message, count, snapshot
                    FROM events
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def count(self) -> int:
        connection = self._connect()
        try:
            with self._lock:
                row = connection.execute("SELECT COUNT(*) AS total FROM events").fetchone()
            return int(row["total"])
        finally:
            connection.close()

    def clear(self) -> None:
        """Delete every stored event (used before a clean demo run)."""
        connection = self._connect()
        try:
            with self._lock, connection:
                connection.execute("DELETE FROM events")
        finally:
            connection.close()
