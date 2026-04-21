"""
Redis pub/sub → WebSocket fan-out subscriber.

Runs as a long-lived asyncio background task started in the FastAPI
lifespan context. Subscribes to the pattern `prices:*` on Redis and
for every received price tick:

  1. Broadcasts the raw tick to all WS clients subscribed to
     `prices:{SYMBOL}:{EXCHANGE}` (chart live-update).

  2. Calls PnlTracker.on_tick() to compute portfolio P&L deltas and
     push them to `portfolio:{portfolio_id}` WS channels.

  3. Calls AlertEvaluator.on_tick() to evaluate alert conditions and
     push notifications to `alerts:{user_id}` WS channels.

Redis price tick payload (published by trading engine or refresh jobs):
{
  "symbol":    "AAPL",
  "exchange":  "NASDAQ",
  "close":     189.50,
  "open":      188.0,
  "high":      190.2,
  "low":       187.5,
  "volume":    65432100,
  "ts":        "2026-04-17T21:30:00Z",
  "frequency": "daily"
}

Publisher utility (used by daily_refresh, on-demand fetch, trading engine):
  import redis
  r = redis.Redis(host=..., port=...)
  r.publish("prices:AAPL:NASDAQ", json.dumps(tick_dict))
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def price_subscriber_loop(
    redis_host: str,
    redis_port: int,
    manager,        # ConnectionManager
    pnl_tracker,    # PnlTracker
    alert_evaluator, # AlertEvaluator
) -> None:
    """
    Long-running coroutine. Subscribes to Redis `prices:*` pattern and
    dispatches ticks to the manager, P&L tracker, and alert evaluator.

    Reconnects automatically with exponential back-off on Redis failure.
    """
    backoff = 1.0
    while True:
        try:
            import redis.asyncio as aioredis

            client = aioredis.Redis(host=redis_host, port=redis_port)
            pubsub = client.pubsub()
            await pubsub.psubscribe("prices:*")
            logger.info("Redis subscriber connected — listening on prices:*")
            backoff = 1.0  # reset on successful connect

            async for raw in pubsub.listen():
                if raw["type"] != "pmessage":
                    continue

                try:
                    data: dict = json.loads(raw["data"])
                except (json.JSONDecodeError, TypeError):
                    continue

                symbol   = data.get("symbol", "")
                exchange = data.get("exchange", "")
                price    = data.get("close")
                ts       = data.get("ts", "")

                if not symbol or not exchange or price is None:
                    continue

                channel = f"prices:{symbol}:{exchange}"

                # 1 — Fan out raw tick to chart subscribers
                if manager.subscriber_count(channel) > 0:
                    await manager.broadcast(channel, {
                        "type": "price_tick",
                        "channel": channel,
                        "payload": data,
                    })

                # 2 — Portfolio P&L deltas
                await pnl_tracker.on_tick(symbol, exchange, float(price), ts)

                # 3 — Alert evaluation
                await alert_evaluator.on_tick(symbol, exchange, float(price), ts)

        except asyncio.CancelledError:
            logger.info("Redis subscriber task cancelled — shutting down.")
            return
        except Exception as exc:
            logger.warning(
                "Redis subscriber error (retry in %.0fs): %s", backoff, exc
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
