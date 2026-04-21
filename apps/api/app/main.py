import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from app.api.routes.alerts import router as alerts_router
from app.api.routes.annotations import router as annotations_router
from app.api.routes.auth import router as auth_router
from app.api.routes.backtests import router as backtests_router
from app.api.routes.bars import router as bars_router
from app.api.routes.broker_accounts import router as broker_accounts_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.dashboards import router as dashboards_router
from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.indicators import router as indicators_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.market_metrics import router as market_metrics_router
from app.api.routes.portfolios import router as portfolios_router
from app.api.routes.strategies import router as strategies_router
from app.api.routes.strategy_control import router as strategy_control_router
from app.api.routes.users import router as users_router
from app.auth.jwt import decode_access_token
from app.core.config import settings
from app.lib.arq_pool import create_arq_pool
from app.lib.cache import RedisBarCache, make_redis_client
from app.ws.alert_evaluator import AlertEvaluator
from app.ws.manager import ConnectionManager
from app.ws.pnl_tracker import PnlTracker
from app.ws.redis_subscriber import price_subscriber_loop

logger = logging.getLogger(__name__)
manager = ConnectionManager()


def _pg_settings() -> dict:
    return dict(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ------------------------------------------------------------------ startup

    # Redis bar cache (sync client, thread-safe across route handler threads)
    if settings.cache_enabled:
        redis_client = make_redis_client(settings.redis_host, settings.redis_port)
        app.state.bar_cache = RedisBarCache(redis_client) if redis_client else None
    else:
        app.state.bar_cache = None

    # ARQ async pool (for enqueueing on-demand jobs from route handlers)
    app.state.arq_pool = await create_arq_pool(settings.redis_host, settings.redis_port)

    # Redis pub/sub → WS fan-out subscriber task
    pg = _pg_settings()
    pnl_tracker     = PnlTracker(pg_settings=pg, manager=manager)
    alert_evaluator = AlertEvaluator(pg_settings=pg, manager=manager)
    subscriber_task = asyncio.create_task(
        price_subscriber_loop(
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
            manager=manager,
            pnl_tracker=pnl_tracker,
            alert_evaluator=alert_evaluator,
        ),
        name="redis-price-subscriber",
    )
    app.state.subscriber_task = subscriber_task

    yield

    # ------------------------------------------------------------------ shutdown
    subscriber_task.cancel()
    try:
        await subscriber_task
    except asyncio.CancelledError:
        pass

    if app.state.arq_pool:
        await app.state.arq_pool.aclose()


app = FastAPI(title="Finance Trading API", lifespan=lifespan)

# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------
app.include_router(health_router)
app.include_router(auth_router,           prefix="/api/v1")
app.include_router(users_router,          prefix="/api/v1")
app.include_router(dashboard_router,      prefix="/api/v1")
app.include_router(dashboards_router,     prefix="/api/v1")
app.include_router(strategy_control_router, prefix="/api/v1")
app.include_router(bars_router,           prefix="/api/v1")
app.include_router(indicators_router,     prefix="/api/v1")
app.include_router(events_router,         prefix="/api/v1")
app.include_router(annotations_router,    prefix="/api/v1")
app.include_router(alerts_router,         prefix="/api/v1")
app.include_router(broker_accounts_router, prefix="/api/v1")
app.include_router(portfolios_router,     prefix="/api/v1")
app.include_router(strategies_router,     prefix="/api/v1")
app.include_router(backtests_router,      prefix="/api/v1")
app.include_router(jobs_router,           prefix="/api/v1")
app.include_router(market_metrics_router, prefix="/api/v1")


# ------------------------------------------------------------------
# WebSocket endpoint — JWT required via ?token= query param
# ------------------------------------------------------------------

_WS_USER_CHANNELS = ("portfolio:", "alerts:")


@app.websocket("/ws/{channel:path}")
async def websocket_endpoint(
    websocket: WebSocket,
    channel: str,
    token: str | None = Query(default=None),
) -> None:
    """
    Authenticated WebSocket endpoint.

    channel formats:
      prices:AAPL:NASDAQ          — live price tick (any authenticated user)
      portfolio:{portfolio_id}    — live P&L updates (owner only)
      alerts:{user_id}            — alert notifications (self only)

    Auth: pass the JWT as ?token=<access_token> in the WS URL.
    The token is the same one returned by POST /auth/login.
    """
    # --- Authenticate ---
    current_user_id: str | None = None
    if token:
        try:
            payload = decode_access_token(token)
            current_user_id = payload.get("sub")
        except Exception:
            await websocket.close(code=1008, reason="Invalid token")
            return
    else:
        await websocket.close(code=1008, reason="Token required")
        return

    # --- Validate ownership for user-scoped channels ---
    if channel.startswith("alerts:"):
        # alerts:{user_id} — must match the authenticated user
        channel_user = channel.split(":", 1)[1]
        if channel_user != current_user_id:
            await websocket.close(code=1008, reason="Forbidden")
            return

    # portfolio:{portfolio_id} ownership is DB-checked lazily on first P&L push
    # (any data sent to an unowned portfolio channel is silently ignored)

    await manager.connect(channel, websocket, user_id=current_user_id)
    logger.debug("WS connected: channel=%s user=%s", channel, current_user_id)

    try:
        while True:
            # Keep the connection alive; the server pushes data proactively.
            # Accept and discard any client-side messages (e.g. ping frames).
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(channel, websocket)
        logger.debug("WS disconnected: channel=%s user=%s", channel, current_user_id)


@app.get("/")
async def root() -> dict:
    return {"service": "finance-trading-api", "status": "running"}
