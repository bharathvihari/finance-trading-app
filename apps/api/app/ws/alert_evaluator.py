"""
Alert condition evaluator.

On each price tick, fetches all active alerts for the ticked symbol and
evaluates their conditions. Triggered alerts are:
  1. Marked as 'triggered' in Postgres.
  2. Pushed as a notification to the user's `alerts:{user_id}` WS channel.

Supported condition types (stored in alerts.condition_json):
  {"type": "price_above",       "threshold": 200.0}
  {"type": "price_below",       "threshold": 150.0}
  {"type": "pct_change_above",  "threshold": 0.05,  "reference_price": 180.0}
  {"type": "pct_change_below",  "threshold": -0.05, "reference_price": 180.0}

Pushed message shape (channel: alerts:{user_id}):
{
  "type":             "alert_triggered",
  "alert_id":         "<uuid>",
  "user_id":          "<uuid>",
  "symbol":           "AAPL",
  "exchange":         "NASDAQ",
  "condition_type":   "price_above",
  "threshold":        200.0,
  "triggered_price":  201.50,
  "message":          "AAPL crossed above 200.0",
  "ts":               "2026-04-17T21:30:00Z"
}
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _fetch_active_alerts(pg_settings: dict, symbol: str, exchange: str) -> list[dict]:
    """Synchronous DB query — runs in a thread pool."""
    try:
        import psycopg2
    except ModuleNotFoundError:
        return []
    try:
        conn = psycopg2.connect(**pg_settings)
    except Exception as exc:
        logger.debug("AlertEvaluator DB connect failed: %s", exc)
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, condition_json, alert_type, message
                FROM alerts
                WHERE symbol = %s AND exchange = %s AND status = 'active';
                """,
                [symbol, exchange],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "alert_id":       str(row[0]),
            "user_id":        str(row[1]),
            "condition_json": row[2] or {},
            "alert_type":     row[3],
            "message":        row[4],
        }
        for row in rows
    ]


def _mark_triggered(pg_settings: dict, alert_id: str, ts: str) -> None:
    """Synchronous DB write — runs in a thread pool."""
    try:
        import psycopg2
    except ModuleNotFoundError:
        return
    try:
        conn = psycopg2.connect(**pg_settings)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE alerts
                SET status='triggered', triggered_at=%s
                WHERE id=%s AND status='active';
                """,
                [ts, alert_id],
            )
        conn.commit()
    except Exception as exc:
        logger.debug("AlertEvaluator mark_triggered failed: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _evaluate_condition(condition: dict, price: float) -> bool:
    """Pure condition check — no I/O."""
    ctype     = condition.get("type", "")
    threshold = condition.get("threshold")
    if threshold is None:
        return False

    if ctype == "price_above":
        return price > float(threshold)
    if ctype == "price_below":
        return price < float(threshold)
    if ctype in ("pct_change_above", "pct_change_below"):
        ref = condition.get("reference_price")
        if not ref:
            return False
        pct = (price - float(ref)) / float(ref)
        if ctype == "pct_change_above":
            return pct > float(threshold)
        return pct < float(threshold)
    return False


def _build_notification(alert: dict, price: float, ts: str) -> dict:
    cond = alert["condition_json"]
    ctype = cond.get("type", "custom")
    threshold = cond.get("threshold", "")
    msg = alert.get("message") or f"{alert['alert_id'][:8]} triggered at {price}"
    return {
        "type":           "alert_triggered",
        "alert_id":       alert["alert_id"],
        "user_id":        alert["user_id"],
        "condition_type": ctype,
        "threshold":      threshold,
        "triggered_price": price,
        "message":        msg,
        "ts":             ts,
    }


class AlertEvaluator:
    """Injected into the Redis subscriber loop at startup."""

    def __init__(self, pg_settings: dict, manager) -> None:
        self._pg = pg_settings
        self._manager = manager

    async def on_tick(
        self,
        symbol: str,
        exchange: str,
        price: float,
        ts: str,
    ) -> None:
        """Evaluate all active alerts for this symbol. Non-blocking."""
        alerts = await asyncio.to_thread(
            _fetch_active_alerts, self._pg, symbol, exchange
        )
        if not alerts:
            return

        now_ts = ts or datetime.now(timezone.utc).isoformat()

        for alert in alerts:
            if not _evaluate_condition(alert["condition_json"], price):
                continue

            # Mark triggered in DB (fire-and-forget in thread)
            asyncio.create_task(
                asyncio.to_thread(_mark_triggered, self._pg, alert["alert_id"], now_ts)
            )

            # Push notification to user's WS channel
            channel = f"alerts:{alert['user_id']}"
            notification = _build_notification(alert, price, now_ts)
            notification["symbol"]   = symbol
            notification["exchange"] = exchange

            if self._manager.subscriber_count(channel) > 0:
                await self._manager.broadcast(channel, notification)
            else:
                # User not connected — could queue in Redis for next connect
                logger.info(
                    "Alert triggered but user not connected: user=%s alert=%s",
                    alert["user_id"], alert["alert_id"],
                )
