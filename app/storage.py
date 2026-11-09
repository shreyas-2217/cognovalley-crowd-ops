"""SQLite event history.

One table, parameterized SQL, and a lock because the inference thread
writes while Flask reads.
"""

# TEACHING NOTE: defers evaluation of type hints.
from __future__ import annotations

import sqlite3      # stdlib database; no server, just a file
import threading    # Lock, because two threads touch the same database
from pathlib import Path    # create the parent directory if missing

# Storage only needs the Event container; it does not need RuleEngine.
from app.rules import Event


class EventStore:
    """Persists events and hands the newest ones back to the API."""

    def __init__(self, db_path: str | Path) -> None:
        # str() so sqlite3.connect always receives a plain string.
        self.db_path = str(db_path)
        # Make sure data/ exists before SQLite tries to create the file inside it.
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # One lock shared by every operation. The worker thread writes while
        # Flask request threads read, so all access is serialised through it.
        self._lock = threading.Lock()
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Each call gets its own, closed by the caller."""
        # check_same_thread=False allows a connection created here to be used
        # on the thread that asked for it (whichever that is).
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        # Rows behave like dicts, so `row["zone"]` works instead of row[2].
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        """Create the table and index if they do not exist yet."""
        connection = self._connect()
        try:
            # Column meanings:
            #   id       - unique row id, auto-assigned
            #   ts       - unix timestamp in seconds
            #   zone     - zone name the event belongs to
            #   type     - warning | capacity | dwell
            #   severity - warning | critical | info (drives the UI colour)
            #   message  - human-readable sentence shown in the log
            #   count    - how many people were in the zone at that moment
            #   snapshot - evidence JPEG filename, or NULL
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
                # The dashboard always asks for newest-first, so index that column.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC)"
                )
        finally:
            # Always close, even if the CREATE failed.
            connection.close()

    def add(self, event: Event, snapshot: str | None = None) -> int:
        """Insert one event and return its new row id."""
        connection = self._connect()
        try:
            # Both context managers: the lock serialises threads, and the
            # connection context commits (or rolls back) the transaction.
            with self._lock, connection:
                # The ? placeholders are filled by sqlite3, never by string
                # formatting, which makes SQL injection impossible.
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
                # lastrowid is the AUTOINCREMENT id SQLite just assigned.
                return int(cursor.lastrowid)
        finally:
            connection.close()

    def recent(self, limit: int = 50) -> list[dict]:
        """Return the newest `limit` events as plain dicts."""
        connection = self._connect()
        try:
            with self._lock:
                # Newest first; the id tie-break keeps ordering deterministic
                # when two events share a timestamp.
                rows = connection.execute(
                    """
                    SELECT id, ts, zone, type, severity, message, count, snapshot
                    FROM events
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    # int() and the bound parameter keep this injection-proof.
                    (int(limit),),
                ).fetchall()
            # sqlite3.Row -> real dict, so Flask's jsonify can serialise it.
            return [dict(row) for row in rows]
        finally:
            connection.close()

    def count(self) -> int:
        """Total number of stored events."""
        connection = self._connect()
        try:
            with self._lock:
                row = connection.execute("SELECT COUNT(*) AS total FROM events").fetchone()
            # fetchone() always returns a row here (COUNT never returns nothing).
            return int(row["total"])
        finally:
            connection.close()

    def clear(self) -> None:
        """Delete every stored event (used before a clean demo run)."""
        connection = self._connect()
        try:
            with self._lock, connection:
                # Deletes rows only. The evidence JPEGs are purged separately
                # by main.py, which owns the snapshot directory.
                connection.execute("DELETE FROM events")
        finally:
            connection.close()
