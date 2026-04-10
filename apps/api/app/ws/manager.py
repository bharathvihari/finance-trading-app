from collections import defaultdict
from typing import DefaultDict, Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: DefaultDict[str, Set[WebSocket]] = defaultdict(set)

    async def connect(self, channel: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[channel].add(websocket)

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        self._connections[channel].discard(websocket)

    async def broadcast(self, channel: str, message: dict) -> None:
        stale = []
        for ws in self._connections[channel]:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._connections[channel].discard(ws)
