"""
Tiered bar data reader — queries the hot (Postgres) and cold (Parquet/DuckDB)
stores and merges results transparently, with optional Redis caching.

Tier boundary
-------------
hot_cutoff = first day of the month that is `hot_window_months` before today
    - bars with timestamp >= hot_cutoff  →  Postgres  (hot tier)
    - bars with timestamp <  hot_cutoff  →  Parquet   (cold tier)

Cache layer
-----------
If a RedisBarCache is injected, read() checks the cache first and populates
it on miss. Cache is entirely optional — if Redis is down, reads fall through
transparently to the storage tiers. latest_price() is never cached because
it is intentionally short-ranged and always needs fresh data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_DEDUP_KEY = ["symbol", "exchange", "asset_class", "frequency", "timestamp"]


def _hot_cutoff(hot_window_months: int) -> datetime:
    """Return the UTC month-boundary that separates hot from cold data."""
    now = datetime.now(timezone.utc)
    month = now.month - hot_window_months
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


class BarReader:
    """
    Self-contained reader for bar data that abstracts the two-tier storage.

    Parameters match the API Settings model; the reader holds no FastAPI
    dependencies so it can be unit-tested in isolation.
    """

    def __init__(
        self,
        parquet_root: str,
        hot_window_months: int,
        postgres_enabled: bool = False,
        postgres_host: str = "127.0.0.1",
        postgres_port: int = 5432,
        postgres_db: str = "trading_app",
        postgres_user: str = "trading_user",
        postgres_password: str = "trading_pass",
        postgres_schema: str = "market_data",
        postgres_bars_table: str = "daily_bars",
        cache=None,          # Optional[RedisBarCache]
    ) -> None:
        self._parquet_root = Path(parquet_root)
        self._hot_window_months = hot_window_months
        self._pg_enabled = postgres_enabled
        self._pg_host = postgres_host
        self._pg_port = postgres_port
        self._pg_db = postgres_db
        self._pg_user = postgres_user
        self._pg_password = postgres_password
        self._pg_schema = postgres_schema
        self._pg_table = postgres_bars_table
        self._cache = cache   # RedisBarCache | None

    # ------------------------------------------------------------------
    # Cold path — Parquet via DuckDB
    # ------------------------------------------------------------------

    def _read_cold(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        start_utc: datetime | None,
        end_utc: datetime | None,
    ) -> pd.DataFrame:
        try:
            import duckdb
        except ModuleNotFoundError:
            logger.warning("duckdb not installed; cold read skipped")
            return pd.DataFrame()

        if not list(self._parquet_root.rglob("*.parquet")):
            return pd.DataFrame()

        pattern = str(self._parquet_root / "**" / "*.parquet").replace("\\", "/")

        where = ["symbol = ?", "exchange = ?", "asset_class = ?", "frequency = ?"]
        params: list[object] = [symbol, exchange, asset_class, frequency]

        if start_utc is not None:
            where.append("timestamp >= ?")
            params.append(start_utc)
        if end_utc is not None:
            where.append("timestamp <= ?")
            params.append(end_utc)

        query = f"""
            SELECT symbol, exchange, asset_class, frequency,
                   timestamp, open, high, low, close, volume
            FROM read_parquet(?, hive_partitioning=true)
            WHERE {' AND '.join(where)}
            ORDER BY timestamp ASC;
        """

        conn = duckdb.connect(":memory:")
        try:
            df = conn.execute(query, [pattern, *params]).df()
        finally:
            conn.close()

        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    # ------------------------------------------------------------------
    # Hot path — Postgres
    # ------------------------------------------------------------------

    def _read_hot(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        start_utc: datetime | None,
        end_utc: datetime | None,
    ) -> pd.DataFrame:
        if not self._pg_enabled:
            return pd.DataFrame()

        try:
            import psycopg2
        except ModuleNotFoundError:
            logger.warning("psycopg2 not installed; hot read skipped")
            return pd.DataFrame()

        qualified = f'"{self._pg_schema}"."{self._pg_table}"'

        where = [
            "symbol = %s",
            "exchange = %s",
            "asset_class = %s",
            "frequency = %s",
        ]
        params: list[object] = [symbol, exchange, asset_class, frequency]

        if start_utc is not None:
            where.append("timestamp >= %s")
            params.append(start_utc)
        if end_utc is not None:
            where.append("timestamp <= %s")
            params.append(end_utc)

        query = f"""
            SELECT symbol, exchange, asset_class, frequency,
                   timestamp, open, high, low, close, volume
            FROM {qualified}
            WHERE {' AND '.join(where)}
            ORDER BY timestamp ASC;
        """

        try:
            conn = psycopg2.connect(
                host=self._pg_host,
                port=self._pg_port,
                dbname=self._pg_db,
                user=self._pg_user,
                password=self._pg_password,
            )
        except Exception as exc:
            logger.warning("Postgres connection failed, hot read skipped: %s", exc)
            return pd.DataFrame()

        try:
            df = pd.read_sql_query(query, conn, params=params)
        finally:
            conn.close()

        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    # ------------------------------------------------------------------
    # Public interface — tiered dispatch with cache
    # ------------------------------------------------------------------

    def read(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """
        Return a merged, deduplicated, time-ordered DataFrame for the given
        instrument and optional date range.

        Cache hit  → return immediately (no storage I/O).
        Cache miss → query tiers, store result in cache before returning.

        Routing logic (after cache miss):
            end   <= cutoff                 →  cold only
            start >= cutoff                 →  hot only
            start < cutoff <= end (or open) →  cold + hot merged
        """
        # --- Cache check ---
        cache_key: str | None = None
        if self._cache is not None:
            from app.lib.cache import RedisBarCache
            cache_key = RedisBarCache.build_key(
                symbol, exchange, asset_class, frequency, start_utc, end_utc
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.debug("cache hit: %s", cache_key)
                return cached

        # --- Tiered read ---
        cutoff = _hot_cutoff(self._hot_window_months)

        need_cold = start_utc is None or start_utc < cutoff
        need_hot = self._pg_enabled and (end_utc is None or end_utc >= cutoff)

        frames: list[pd.DataFrame] = []

        if need_cold:
            cold_end = min(end_utc, cutoff) if end_utc is not None else cutoff
            df = self._read_cold(symbol, exchange, asset_class, frequency, start_utc, cold_end)
            if not df.empty:
                frames.append(df)

        if need_hot:
            hot_start = max(start_utc, cutoff) if start_utc is not None else cutoff
            df = self._read_hot(symbol, exchange, asset_class, frequency, hot_start, end_utc)
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=_DEDUP_KEY, keep="last")
        combined = combined.sort_values("timestamp").reset_index(drop=True)

        # --- Populate cache ---
        if self._cache is not None and cache_key and not combined.empty:
            self._cache.set(cache_key, combined, end_utc)

        return combined

    def latest_price(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str = "daily",
    ) -> float | None:
        """Return the most recent close price for an instrument.

        Fetches only the last 10 calendar days to minimise I/O — enough to
        span weekends and public holidays. Not cached: always needs fresh data.
        """
        start = datetime.now(timezone.utc) - timedelta(days=10)
        # Bypass cache: call internal methods directly
        cutoff = _hot_cutoff(self._hot_window_months)
        frames: list[pd.DataFrame] = []

        cold_end = min(datetime.now(timezone.utc), cutoff)
        if start < cutoff:
            df = self._read_cold(symbol, exchange, asset_class, frequency, start, cold_end)
            if not df.empty:
                frames.append(df)

        if self._pg_enabled:
            hot_start = max(start, cutoff)
            df = self._read_hot(symbol, exchange, asset_class, frequency,
                                hot_start, None)
            if not df.empty:
                frames.append(df)

        if not frames:
            return None

        combined = pd.concat(frames, ignore_index=True).sort_values("timestamp")
        return float(combined["close"].iloc[-1])
