"""
Portfolio P&L tracker.

On each price tick, queries all open positions for the ticked symbol,
computes the unrealized P&L delta, and pushes an update to each affected
portfolio's WebSocket channel via the ConnectionManager.

Database access is synchronous (psycopg2) — wrapped in asyncio.to_thread()
so it does not block the event loop.

Pushed message shape (channel: portfolio:{portfolio_id}):
{
  "type":          "pnl_update",
  "portfolio_id":  "<uuid>",
  "symbol":        "AAPL",
  "exchange":      "NASDAQ",
  "current_price": 189.50,
  "position_updates": [
    {
      "position_id":       "<uuid>",
      "quantity":          100.0,
      "cost_basis":        165.0,
      "current_value":     18950.0,
      "unrealized_pnl":    2450.0,
      "unrealized_pnl_pct": 0.1485
    }
  ],
  "portfolio_unrealized_pnl": 2450.0,
  "ts": "2026-04-17T21:30:00Z"
}
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _fetch_affected_positions(pg_settings: dict, symbol: str, exchange: str) -> list[dict]:
    """
    Synchronous DB query — runs in a thread pool via asyncio.to_thread().
    Returns list of {portfolio_id, user_id, position_id, quantity, cost_basis}.
    """
    try:
        import psycopg2
    except ModuleNotFoundError:
        return []

    try:
        conn = psycopg2.connect(**pg_settings)
    except Exception as exc:
        logger.debug("PnlTracker DB connect failed: %s", exc)
        return []

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.portfolio_id, p.user_id,
                       p.quantity, p.cost_basis
                FROM positions p
                WHERE p.symbol = %s AND p.exchange = %s
                  AND p.closed_at IS NULL;
                """,
                [symbol, exchange],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {
            "position_id":  str(row[0]),
            "portfolio_id": str(row[1]),
            "user_id":      str(row[2]),
            "quantity":     float(row[3]),
            "cost_basis":   float(row[4]),
        }
        for row in rows
    ]


class PnlTracker:
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
        """Called for every price tick. Non-blocking — DB query runs in thread."""
        positions = await asyncio.to_thread(
            _fetch_affected_positions, self._pg, symbol, exchange
        )
        if not positions:
            return

        # Group by portfolio
        by_portfolio: dict[str, list[dict]] = {}
        for pos in positions:
            pid = pos["portfolio_id"]
            by_portfolio.setdefault(pid, []).append(pos)

        now_ts = ts or datetime.now(timezone.utc).isoformat()

        for portfolio_id, plist in by_portfolio.items():
            updates = []
            total_pnl = 0.0
            for pos in plist:
                current_value = pos["quantity"] * price
                cost_total    = pos["quantity"] * pos["cost_basis"]
                pnl           = current_value - cost_total
                pnl_pct       = pnl / cost_total if cost_total else 0.0
                total_pnl    += pnl
                updates.append({
                    "position_id":        pos["position_id"],
                    "quantity":           pos["quantity"],
                    "cost_basis":         pos["cost_basis"],
                    "current_value":      round(current_value, 4),
                    "unrealized_pnl":     round(pnl, 4),
                    "unrealized_pnl_pct": round(pnl_pct, 6),
                })

            channel = f"portfolio:{portfolio_id}"
            if self._manager.subscriber_count(channel) > 0:
                await self._manager.broadcast(channel, {
                    "type":                     "pnl_update",
                    "portfolio_id":             portfolio_id,
                    "symbol":                   symbol,
                    "exchange":                 exchange,
                    "current_price":            price,
                    "position_updates":         updates,
                    "portfolio_unrealized_pnl": round(total_pnl, 4),
                    "ts":                       now_ts,
                })
