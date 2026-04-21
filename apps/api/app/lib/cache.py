"""
Redis cache for DataFrame results.

Serialization strategy: Parquet via pyarrow.
  - Preserves all dtypes including timezone-aware timestamps exactly.
  - Compact columnar compression — 1 year of daily OHLCV ≈ 5–15 KB per symbol.
  - Serialize/deserialize overhead < 5 ms for typical bar datasets.

TTL strategy:
  - end_utc is None or today/future  →  SHORT_TTL (5 min)  — data may update intraday
  - end_utc more than 2 days ago     →  LONG_TTL  (1 hour) — historical, effectively immutable

The cache is entirely optional. If Redis is unavailable the caller receives
None from get() and set() is a no-op. BarReader degrades gracefully.
"""
from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

SHORT_TTL = 300      # 5 minutes — live/today-touching ranges
LONG_TTL  = 3_600   # 1 hour    — historical ranges


def _key_hash(parts: list[str]) -> str:
    """Stable, short cache key from variable-length parts."""
    raw = ":".join(parts)
    return "bars:" + hashlib.blake2b(raw.encode(), digest_size=12).hexdigest()


def _choose_ttl(end_utc: datetime | None) -> int:
    """Return SHORT_TTL if the range touches today; LONG_TTL if it's historical."""
    if end_utc is None:
        return SHORT_TTL
    two_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
    return LONG_TTL if end_utc < two_days_ago else SHORT_TTL


def _df_to_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, compression="snappy")
    return buf.getvalue()


def _bytes_to_df(data: bytes) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(data))


class RedisBarCache:
    """
    Thin wrapper around a redis.Redis client scoped to bar data caching.

    Pass an already-connected redis.Redis instance (sync client).
    The caller is responsible for connection lifecycle.
    """

    def __init__(self, client) -> None:
        self._r = client

    @staticmethod
    def build_key(
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        start_utc: datetime | None,
        end_utc: datetime | None,
    ) -> str:
        start_s = start_utc.isoformat() if start_utc else "none"
        end_s   = end_utc.isoformat()   if end_utc   else "none"
        return _key_hash([symbol, exchange, asset_class, frequency, start_s, end_s])

    def get(self, key: str) -> pd.DataFrame | None:
        try:
            data = self._r.get(key)
        except Exception as exc:
            logger.debug("Cache GET error (key=%s): %s", key, exc)
            return None
        if data is None:
            return None
        try:
            return _bytes_to_df(data)
        except Exception as exc:
            logger.debug("Cache deserialise error (key=%s): %s", key, exc)
            return None

    def set(self, key: str, df: pd.DataFrame, end_utc: datetime | None) -> None:
        if df.empty:
            return
        ttl = _choose_ttl(end_utc)
        try:
            self._r.setex(key, ttl, _df_to_bytes(df))
        except Exception as exc:
            logger.debug("Cache SET error (key=%s): %s", key, exc)

    def invalidate(self, key: str) -> None:
        try:
            self._r.delete(key)
        except Exception as exc:
            logger.debug("Cache DEL error (key=%s): %s", key, exc)


def make_redis_client(host: str, port: int):
    """Return a connected redis.Redis client, or None if redis is unavailable."""
    try:
        import redis as redis_lib
        client = redis_lib.Redis(host=host, port=port, socket_connect_timeout=1)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis unavailable — bar cache disabled: %s", exc)
        return None
