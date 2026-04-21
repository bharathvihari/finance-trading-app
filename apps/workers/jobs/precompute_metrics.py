"""
Nightly pre-aggregation of rolling performance metrics per symbol.

For each symbol in the universe, reads the last N years of daily bars from
the tiered store and computes: total_return, CAGR, max_drawdown, volatility,
Sharpe, and Sortino. Results are upserted into market_data.rolling_metrics.

The API reads from this table instead of computing on every request, keeping
p95 latency well under the 500 ms target for popular symbols.

Usage:
    python -m jobs.precompute_metrics                      # full universe
    python -m jobs.precompute_metrics --symbols AAPL MSFT  # specific symbols
    python -m jobs.precompute_metrics --windows 252 756    # specific windows

Also called as an ARQ cron task (see arq_worker.py).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Allow running as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from market_data.config import load_job_config
from market_data.logger import StructuredLogger
from market_data.parquet_store import ParquetStore
from market_data.postgres_store import PostgresBarStore
from market_data.universe_loader import load_universe

# Import metrics lib from the API package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "api"))
from app.lib.metrics import compute_metrics

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_WINDOWS = [252, 504, 756, 1260]   # 1Y, 2Y, 3Y, 5Y


def _hot_cutoff(hot_window_months: int) -> datetime:
    now = datetime.now(timezone.utc)
    month = now.month - hot_window_months
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def _read_bars(
    symbol: str,
    exchange: str,
    asset_class: str,
    parquet_store: ParquetStore,
    hot_store: PostgresBarStore | None,
    hot_cutoff: datetime,
    max_days: int,
) -> pd.Series:
    """Return a date-indexed close price series for the last max_days bars."""
    cold_df = parquet_store.read_bars(
        symbol=symbol, exchange=exchange, frequency="daily",
        asset_class=asset_class,
    )
    frames = []
    if not cold_df.empty:
        frames.append(cold_df)
    if hot_store is not None:
        # Read everything in hot tier for this symbol
        hot_df = hot_store.read_bars_for_partition(
            asset_class=asset_class, exchange=exchange,
            frequency="daily", year=datetime.now(timezone.utc).year,
            cutoff_utc=datetime(9999, 1, 1, tzinfo=timezone.utc),  # no cutoff — read all
        )
        if not hot_df.empty:
            frames.append(hot_df)

    if not frames:
        return pd.Series(dtype=float)

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["symbol", "exchange", "asset_class", "frequency", "timestamp"])
        .sort_values("timestamp")
        .set_index("timestamp")["close"]
    )
    # Keep only the last max_days bars
    return combined.iloc[-max_days:] if len(combined) > max_days else combined


def _upsert_metrics(conn, rows: list[tuple]) -> None:
    """rows: (symbol, exchange, asset_class, frequency, as_of_date, window_days,
              total_return, cagr, max_drawdown, volatility, sharpe, sortino, computed_at)"""
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO market_data.rolling_metrics
                (symbol, exchange, asset_class, frequency, as_of_date, window_days,
                 total_return, cagr, max_drawdown, volatility, sharpe, sortino, computed_at)
            VALUES %s
            ON CONFLICT (symbol, exchange, asset_class, frequency, as_of_date, window_days)
            DO UPDATE SET
                total_return = EXCLUDED.total_return,
                cagr         = EXCLUDED.cagr,
                max_drawdown = EXCLUDED.max_drawdown,
                volatility   = EXCLUDED.volatility,
                sharpe       = EXCLUDED.sharpe,
                sortino      = EXCLUDED.sortino,
                computed_at  = EXCLUDED.computed_at;
            """,
            rows,
        )
    conn.commit()


def run_precompute(
    symbols_filter: list[str] | None = None,
    windows: list[int] | None = None,
) -> None:
    windows = windows or _DEFAULT_WINDOWS
    cfg = load_job_config(_REPO_ROOT / "config" / "market_data.backfill.yaml")
    pg_cfg = cfg.postgres
    universe = load_universe(cfg)
    logger = StructuredLogger(job_name="precompute_metrics",
                              log_dir=_REPO_ROOT / "data" / "logs")
    logger.set_run_id(f"metrics-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}")

    parquet_store = ParquetStore(_REPO_ROOT / cfg.storage.parquet_root)
    hot_cutoff = _hot_cutoff(pg_cfg.hot_window_months) if pg_cfg.enabled else None
    hot_store: PostgresBarStore | None = (
        PostgresBarStore.from_config(pg_cfg) if pg_cfg.enabled else None
    )

    conn = psycopg2.connect(
        host=pg_cfg.host, port=pg_cfg.port, dbname=pg_cfg.database,
        user=pg_cfg.user, password=pg_cfg.password,
    )
    now = datetime.now(timezone.utc)
    as_of_date = now.date()
    total_rows = 0

    try:
        for instrument in universe.instruments:
            if symbols_filter and instrument.symbol not in symbols_filter:
                continue

            max_window = max(windows)
            closes = _read_bars(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                parquet_store=parquet_store,
                hot_store=hot_store,
                hot_cutoff=hot_cutoff,
                max_days=max_window + 5,   # small buffer for weekends
            )

            if closes.empty or len(closes) < 30:
                logger.log("skip_insufficient", symbol=instrument.symbol,
                           bars=len(closes))
                continue

            rows: list[tuple] = []
            for w in windows:
                window_closes = closes.iloc[-w:] if len(closes) >= w else closes
                if len(window_closes) < 30:
                    continue
                m = compute_metrics(window_closes)
                rows.append((
                    instrument.symbol, instrument.exchange, instrument.asset_class,
                    "daily", as_of_date, w,
                    _nan_to_none(m["total_return"]),
                    _nan_to_none(m["cagr"]),
                    _nan_to_none(m["max_drawdown"]),
                    _nan_to_none(m["volatility"]),
                    _nan_to_none(m["sharpe"]),
                    _nan_to_none(m["sortino"]),
                    now,
                ))

            if rows:
                _upsert_metrics(conn, rows)
                total_rows += len(rows)
                logger.log("symbol_done", symbol=instrument.symbol,
                           windows=len(rows))

        print(f"precompute_metrics complete — {total_rows} metric rows upserted.")
        logger.log("job_end", total_rows=total_rows)
    finally:
        conn.close()
        logger.close()


def _nan_to_none(v: float) -> float | None:
    import math
    return None if math.isnan(v) else v


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--windows", nargs="*", type=int)
    args = parser.parse_args()
    run_precompute(symbols_filter=args.symbols, windows=args.windows)
