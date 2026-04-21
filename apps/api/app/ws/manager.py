"""
WebSocket connection manager.

Tracks connections per channel and per user so both broadcast-to-channel
and send-to-user operations are O(1) lookups.

Channel naming conventions:
  prices:{SYMBOL}:{EXCHANGE}      — live price tick for one instrument
  portfolio:{portfolio_id}        — live P&L delta for one portfolio
  alerts:{user_id}                — alert notifications for one user

User-scoped channels (portfolio:*, alerts:*) are validated on connect:
the JWT user_id must own the referenced resource. Price channels are public
(any authenticated user may subscribe).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import DefaultDict
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # channel → set of WebSocket
        self._connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)
        # user_id (str) → set of (channel, WebSocket) — for targeted delivery
        self._user_sockets: DefaultDict[str, set[tuple[str, WebSocket]]] = defaultdict(set)
        # WebSocket → user_id — reverse lookup for cleanup
        self._ws_user: dict[WebSocket, str] = {}

    async def connect(
        self,
        channel: str,
        websocket: WebSocket,
        user_id: str | UUID | None = None,
    ) -> None:
        await websocket.accept()
        self._connections[channel].add(websocket)
        if user_id is not None:
            uid = str(user_id)
            self._user_sockets[uid].add((channel, websocket))
            self._ws_user[websocket] = uid

    def disconnect(self, channel: str, websocket: WebSocket) -> None:
        self._connections[channel].discard(websocket)
        uid = self._ws_user.pop(websocket, None)
        if uid:
            self._user_sockets[uid].discard((channel, websocket))

    def active_channels(self) -> list[str]:
        return [ch for ch, sockets in self._connections.items() if sockets]

    def subscriber_count(self, channel: str) -> int:
        return len(self._connections.get(channel, set()))

    async def broadcast(self, channel: str, message: dict) -> None:
        """Send message to every WebSocket subscribed to `channel`."""
        sockets = self._connections.get(channel)
        if not sockets:
            return
        stale: list[WebSocket] = []
        tasks = []
        for ws in list(sockets):
            tasks.append(_safe_send(ws, message, stale))
        await asyncio.gather(*tasks)
        for ws in stale:
            self._connections[channel].discard(ws)
            uid = self._ws_user.pop(ws, None)
            if uid:
                self._user_sockets[uid].discard((channel, ws))

    async def send_to_user(self, user_id: str | UUID, message: dict) -> None:
        """Send message to ALL channels this user is currently subscribed to."""
        uid = str(user_id)
        entries = list(self._user_sockets.get(uid, set()))
        if not entries:
            return
        stale: list[WebSocket] = []
        tasks = [_safe_send(ws, message, stale) for _, ws in entries]
        await asyncio.gather(*tasks)
        for ws in stale:
            uid2 = self._ws_user.pop(ws, None)
            if uid2:
                for ch, _ws in list(self._user_sockets.get(uid2, set())):
                    if _ws is ws:
                        self._user_sockets[uid2].discard((ch, ws))
                        self._connections[ch].discard(ws)


async def _safe_send(ws: WebSocket, message: dict, stale: list) -> None:
    try:
        await ws.send_json(message)
    except Exception as exc:
        logger.debug("WS send failed (%s): %s", id(ws), exc)
        stale.append(ws)
