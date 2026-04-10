from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.strategy_control import router as strategy_router
from app.ws.manager import ConnectionManager

app = FastAPI(title="Finance Trading API")
manager = ConnectionManager()

app.include_router(health_router)
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(strategy_router, prefix="/api/v1")


@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str) -> None:
    await manager.connect(channel, websocket)
    try:
        while True:
            client_message = await websocket.receive_text()
            await manager.broadcast(
                channel,
                {
                    "channel": channel,
                    "type": "echo",
                    "payload": client_message,
                },
            )
    except WebSocketDisconnect:
        manager.disconnect(channel, websocket)


@app.get("/")
async def root() -> dict:
    return {"service": "finance-trading-api", "status": "running"}
