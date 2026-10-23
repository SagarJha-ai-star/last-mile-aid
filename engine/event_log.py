"""
Append-only event log.

Every state change is recorded as an Event. The log is used for:
- Real-time WebSocket pushes.
- The "Why?" decision panel.
- Deterministic replay in tests.
- The audit trail (GET /events).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import uuid4


@dataclass
class Event:
    """A single immutable entry in the event log."""
    id: str = field(default_factory=lambda: str(uuid4()))
    type: str = ""                       # e.g. "lot_assigned", "lease_expired"
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""                     # human-readable explanation for "Why?" panel
    timestamp: Optional[datetime] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class EventLog:
    """
    Append-only event store.

    - In-memory list for fast access.
    - Optional SQLite persistence for durability across restarts.
    - Subscriber callbacks for real-time pushes (WebSocket, etc.).
    """

    def __init__(self, db_path: Optional[str] = None):
        self._events: list[Event] = []
        self._subscribers: list[Callable[[Event], None]] = []
        self._db: Optional[sqlite3.Connection] = None

        if db_path:
            self._init_db(db_path)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        """Close the SQLite connection (if any)."""
        if self._db:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # SQLite setup
    # ------------------------------------------------------------------
    def _init_db(self, db_path: str) -> None:
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        TEXT PRIMARY KEY,
                type      TEXT NOT NULL,
                payload   TEXT NOT NULL,
                reason    TEXT NOT NULL DEFAULT '',
                timestamp TEXT NOT NULL
            )
        """)
        self._db.commit()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def append(self, event_type: str, payload: dict[str, Any] | None = None,
               reason: str = "") -> Event:
        """Create and store a new event, notifying all subscribers."""
        event = Event(type=event_type, payload=payload or {}, reason=reason)
        self._events.append(event)

        if self._db:
            self._db.execute(
                "INSERT INTO events (id, type, payload, reason, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (event.id, event.type, json.dumps(event.payload),
                 event.reason, event.timestamp.isoformat()),
            )
            self._db.commit()

        for callback in self._subscribers:
            callback(event)

        return event

    def all(self) -> list[Event]:
        """Return all events in insertion order."""
        return list(self._events)

    def by_type(self, event_type: str) -> list[Event]:
        """Return events filtered by type."""
        return [e for e in self._events if e.type == event_type]

    def last(self, n: int = 1) -> list[Event]:
        """Return the last *n* events."""
        return self._events[-n:]

    def clear(self) -> None:
        """Clear in-memory events (for test resets). Does NOT clear SQLite."""
        self._events.clear()

    # ------------------------------------------------------------------
    # Subscribers (for WebSocket / real-time push)
    # ------------------------------------------------------------------
    def subscribe(self, callback: Callable[[Event], None]) -> None:
        """Register a callback invoked on every new event."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Event], None]) -> None:
        """Remove a previously registered callback."""
        self._subscribers = [cb for cb in self._subscribers if cb is not callback]

    # ------------------------------------------------------------------
    # Replay support
    # ------------------------------------------------------------------
    def load_from_db(self) -> None:
        """Reload events from SQLite into memory (for replay)."""
        if not self._db:
            return
        cursor = self._db.execute(
            "SELECT id, type, payload, reason, timestamp FROM events ORDER BY rowid"
        )
        self._events.clear()
        for row in cursor:
            self._events.append(Event(
                id=row[0],
                type=row[1],
                payload=json.loads(row[2]),
                reason=row[3],
                timestamp=datetime.fromisoformat(row[4]),
            ))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dicts(self) -> list[dict[str, Any]]:
        """Serialise all events to plain dicts (for JSON / API responses)."""
        out = []
        for e in self._events:
            d = asdict(e)
            d["timestamp"] = e.timestamp.isoformat() if e.timestamp else None
            out.append(d)
        return out
