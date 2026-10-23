"""
WebSocket connection manager and real-time event broadcaster.

Bridges the append-only EventLog to all connected frontends (Ops, Donor, Rider, Shelter)
so dashboards update with zero polling delay.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
from fastapi import WebSocket

from engine.event_log import Event, EventLog


class ConnectionManager:
    """Manages active WebSocket client connections and pushes real-time events."""

    def __init__(self, event_log: Optional[EventLog] = None):
        self.active_connections: list[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        if event_log:
            event_log.subscribe(self._on_event_logged)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send JSON message to all connected clients."""
        dead_connections: list[WebSocket] = []
        payload_text = json.dumps(message)

        for connection in self.active_connections:
            try:
                await connection.send_text(payload_text)
            except Exception:
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

    def _on_event_logged(self, event: Event) -> None:
        """Callback invoked whenever a new event is added to the EventLog."""
        msg = {
            "type": event.type,
            "id": event.id,
            "payload": event.payload,
            "reason": event.reason,
            "timestamp": event.timestamp.isoformat() if event.timestamp else datetime.now(timezone.utc).isoformat(),
        }

        # If running inside an asyncio event loop, schedule the broadcast
        try:
            loop = self._loop or asyncio.get_running_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(msg), loop)
        except RuntimeError:
            pass
