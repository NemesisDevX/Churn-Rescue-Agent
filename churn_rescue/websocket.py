"""WebSocket connection manager for the live command-center dashboard.

This is intentionally decoupled from the FastAPI app so it can be shared
between HTTP endpoints, background polling tasks, and the WebSocket route
without circular imports.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Keeps a list of active dashboard WebSockets and broadcasts to all."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("Dashboard client connected | total=%d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info("Dashboard client disconnected | total=%d", len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a JSON message to every connected dashboard client."""
        if not self.active_connections:
            return

        payload = json.dumps(message)
        # Iterate over a snapshot so we can safely remove dead sockets.
        for connection in list(self.active_connections):
            try:
                await connection.send_text(payload)
            except Exception as exc:
                logger.warning("Failed to push to a dashboard client: %s", exc)
                self.disconnect(connection)


manager = ConnectionManager()
