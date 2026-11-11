"""SQLite event history."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.rules import Event


class EventStore:
    """Persists events and hands the newest ones back to the API."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # the worker writes while Flask reads; one lock serialises both
        self._lock = threading.Lock()
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Each call gets its own, closed by the caller."""
        # one connection per operation, safe from any thread
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        # rows behave like dicts
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        """Create the table and index if they do not exist yet."""
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
                    # the log is always newest-first
                    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC)"
                )
        finally:
            connection.close()

    def add(self, event: Event, snapshot: str | None = None) -> int:
        """Insert one event and return its new row id."""
        connection = self._connect()
        try:
            with self._lock, connection:
                # parameterised SQL: ? placeholders, never string formatting
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
        """Return the newest `limit` events as plain dicts."""
        connection = self._connect()
        try:
            with self._lock:
                # newest first; LIMIT is a bound parameter
                rows = connection.execute(
                    """
                    SELECT id, ts, zone, type, severity, message, count, snapshot
                    FROM events
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
            # sqlite3.Row -> dict so Flask can serialise it
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def count(self) -> int:
        """Total number of stored events."""
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
