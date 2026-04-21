"""
Fetch corporate events (dividends, splits, earnings dates) from yfinance
and upsert them into market_data.corporate_events.

Run after backfill and periodically (e.g. weekly) to keep events current.

Usage:
    python -m jobs.fetch_events                     # all symbols in config
    python -m jobs.fetch_events --symbols AAPL MSFT # specific symbols
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import yfinance as yf
from psycopg2.extras import execute_values

from market_data.config import load_job_config
from market_data.logger import StructuredLogger
from market_data.universe_loader import load_universe


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _connect(pg_cfg):
    return psycopg2.connect(
        host=pg_cfg.host,
        port=pg_cfg.port,
        dbname=pg_cfg.database,
        user=pg_cfg.user,
        password=pg_cfg.password,
    )


def _upsert_events(conn, rows: list[tuple]) -> int:
    """rows: (symbol, exchange, asset_class, event_type, event_date, value, currency, description)"""
    if not rows:
        return 0
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market_data.corporate_events
                (symbol, exchange, asset_class, event_type, event_date,
                 value, currency, description, fetched_at)
            VALUES %s
            ON CONFLICT (symbol, exchange, event_type, event_date)
            DO UPDATE SET
                value       = EXCLUDED.value,
                currency    = EXCLUDED.currency,
                description = EXCLUDED.description,
                fetched_at  = EXCLUDED.fetched_at;
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def fetch_events_for_symbol(
    symbol: str,
    exchange: str,
    asset_class: str,
    logger: StructuredLogger,
) -> list[tuple]:
    rows: list[tuple] = []
    now = datetime.now(timezone.utc)
    yf_symbol = symbol  # Yahoo Finance uses same format for most US equities

    try:
        ticker = yf.Ticker(yf_symbol)

        # Dividends
        divs = ticker.dividends
        if divs is not None and not divs.empty:
            for ts, amount in divs.items():
                ev_date = ts.date() if hasattr(ts, "date") else ts
                rows.append((symbol, exchange, asset_class, "dividend",
                             ev_date, float(amount), "USD",
                             f"Dividend ${amount:.4f}", now))

        # Splits
        splits = ticker.splits
        if splits is not None and not splits.empty:
            for ts, ratio in splits.items():
                ev_date = ts.date() if hasattr(ts, "date") else ts
                rows.append((symbol, exchange, asset_class, "split",
                             ev_date, float(ratio), None,
                             f"Stock split {ratio:.2f}:1", now))

        # Earnings dates (upcoming + recent)
        try:
            earn = ticker.get_earnings_dates(limit=20)
            if earn is not None and not earn.empty:
                for ts in earn.index:
                    ev_date = ts.date() if hasattr(ts, "date") else ts
                    rows.append((symbol, exchange, asset_class, "earnings",
                                 ev_date, None, None,
                                 "Earnings date", now))
        except Exception:
            pass  # earnings dates not always available

        logger.log("events_fetched", symbol=symbol, exchange=exchange, rows=len(rows))

    except Exception as exc:
        logger.log("events_error", symbol=symbol, exchange=exchange, error=str(exc))

    return rows


def run_fetch_events(symbols_filter: list[str] | None = None) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.backfill.yaml")
    pg_cfg = cfg.postgres

    if not pg_cfg.enabled:
        print("Postgres not enabled — nothing to do.")
        return

    universe = load_universe(cfg)
    logger = StructuredLogger(job_name="fetch_events", log_dir=root / "data" / "logs")
    logger.set_run_id(f"events-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")
    conn = _connect(pg_cfg)

    total = 0
    try:
        for instrument in universe.instruments:
            if symbols_filter and instrument.symbol not in symbols_filter:
                continue
            rows = fetch_events_for_symbol(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                logger=logger,
            )
            total += _upsert_events(conn, rows)

        print(f"fetch_events complete — {total} event rows upserted.")
        logger.log("job_end", total_rows=total)
    finally:
        conn.close()
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", help="Specific symbols to fetch (default: all)")
    args = parser.parse_args()
    run_fetch_events(symbols_filter=args.symbols)
