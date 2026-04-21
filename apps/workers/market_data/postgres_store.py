from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd

try:
    import psycopg2
    from psycopg2.extras import execute_values
except Exception:  # pragma: no cover - optional runtime dependency in lightweight envs
    psycopg2 = None  # type: ignore[assignment]
    execute_values = None  # type: ignore[assignment]


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validated_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER_PATTERN.match(value):
        raise ValueError(f"Invalid SQL identifier for {label}: '{value}'")
    return value


class PostgresBarStore:
    """Postgres-backed hot storage for recent daily bars."""

    REQUIRED_COLUMNS = {"symbol", "timestamp", "open", "high", "low", "close", "volume", "asset_class", "exchange", "frequency"}

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema: str = "market_data",
        table: str = "daily_bars",
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = _validated_identifier(schema, "schema")
        self.table = _validated_identifier(table, "table")

    @classmethod
    def from_config(cls, cfg: Any) -> PostgresBarStore:
        return cls(
            host=cfg.host,
            port=int(cfg.port),
            database=cfg.database,
            user=cfg.user,
            password=cfg.password,
            schema=getattr(cfg, "schema_name", getattr(cfg, "schema", "market_data")),
            table=cfg.bars_table,
        )

    @property
    def _qualified_table(self) -> str:
        return f'"{self.schema}"."{self.table}"'

    def _connect(self):
        if psycopg2 is None:
            raise ModuleNotFoundError("psycopg2 is required for PostgresBarStore operations.")
        return psycopg2.connect(
            host=self.host,
            port=self.port,
            dbname=self.database,
            user=self.user,
            password=self.password,
        )

    def init_schema(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}";')
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._qualified_table} (
                        symbol TEXT NOT NULL,
                        exchange TEXT NOT NULL,
                        asset_class TEXT NOT NULL,
                        frequency TEXT NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        open DOUBLE PRECISION NOT NULL,
                        high DOUBLE PRECISION NOT NULL,
                        low DOUBLE PRECISION NOT NULL,
                        close DOUBLE PRECISION NOT NULL,
                        volume DOUBLE PRECISION NOT NULL,
                        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (symbol, exchange, asset_class, frequency, timestamp)
                    );
                    """
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table}_timestamp
                    ON {self._qualified_table} (timestamp);
                    """
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table}_symbol_ts
                    ON {self._qualified_table} (symbol, exchange, asset_class, frequency, timestamp DESC);
                    """
                )
            conn.commit()
        finally:
            conn.close()

    def _normalize_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        missing = self.REQUIRED_COLUMNS.difference(df.columns)
        if missing:
            raise ValueError(f"Missing required bar columns: {sorted(missing)}")

        out = df.copy()
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        for col in ("asset_class", "exchange", "frequency", "symbol"):
            out[col] = out[col].astype(str)

        out = out.sort_values(["asset_class", "exchange", "frequency", "symbol", "timestamp"])
        out = out.drop_duplicates(subset=["symbol", "exchange", "asset_class", "frequency", "timestamp"], keep="last")
        return out

    def upsert_bars(self, df: pd.DataFrame) -> int:
        normalized = self._normalize_bars(df)
        if normalized.empty:
            return 0
        if execute_values is None:
            raise ModuleNotFoundError("psycopg2 is required for PostgresBarStore operations.")

        payload = list(
            normalized[
                ["symbol", "exchange", "asset_class", "frequency", "timestamp", "open", "high", "low", "close", "volume"]
            ].itertuples(index=False, name=None)
        )

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""
                    INSERT INTO {self._qualified_table}
                        (symbol, exchange, asset_class, frequency, timestamp, open, high, low, close, volume)
                    VALUES %s
                    ON CONFLICT (symbol, exchange, asset_class, frequency, timestamp)
                    DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume,
                        updated_at = NOW();
                    """,
                    payload,
                    page_size=1000,
                )
            conn.commit()
        finally:
            conn.close()

        return len(payload)

    def fetch_cold_partition_keys(self, cutoff_utc: datetime) -> list[dict]:
        """Return distinct (asset_class, exchange, frequency, year) tuples that have
        bars older than cutoff_utc, ordered so oldest partitions are archived first."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        asset_class,
                        exchange,
                        frequency,
                        EXTRACT(YEAR FROM timestamp)::INT AS year,
                        COUNT(*) AS row_count
                    FROM {self._qualified_table}
                    WHERE timestamp < %s
                    GROUP BY asset_class, exchange, frequency,
                             EXTRACT(YEAR FROM timestamp)::INT
                    ORDER BY asset_class, exchange, frequency, year;
                    """,
                    [cutoff_utc],
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        return [
            {
                "asset_class": r[0],
                "exchange": r[1],
                "frequency": r[2],
                "year": int(r[3]),
                "row_count": int(r[4]),
            }
            for r in rows
        ]

    def read_bars_for_partition(
        self,
        asset_class: str,
        exchange: str,
        frequency: str,
        year: int,
        cutoff_utc: datetime,
    ) -> "pd.DataFrame":
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT symbol, exchange, asset_class, frequency,
                           timestamp, open, high, low, close, volume
                    FROM {self._qualified_table}
                    WHERE asset_class = %s AND exchange = %s
                      AND frequency = %s
                      AND EXTRACT(YEAR FROM timestamp)::INT = %s
                      AND timestamp < %s
                    ORDER BY timestamp ASC;
                    """,
                    [asset_class, exchange, frequency, year, cutoff_utc],
                )
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
        finally:
            conn.close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=cols)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    def delete_bars_for_partition(
        self,
        asset_class: str,
        exchange: str,
        frequency: str,
        year: int,
        cutoff_utc: datetime,
    ) -> int:
        """Delete all rows for a partition that are older than cutoff_utc.
        Returns the number of rows deleted."""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    DELETE FROM {self._qualified_table}
                    WHERE asset_class = %s AND exchange = %s
                      AND frequency = %s
                      AND EXTRACT(YEAR FROM timestamp)::INT = %s
                      AND timestamp < %s;
                    """,
                    [asset_class, exchange, frequency, year, cutoff_utc],
                )
                deleted = cur.rowcount
            conn.commit()
        finally:
            conn.close()

        return deleted

    def latest_timestamp(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> datetime | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT MAX(timestamp)
                    FROM {self._qualified_table}
                    WHERE symbol = %s AND exchange = %s AND asset_class = %s AND frequency = %s;
                    """,
                    [symbol, exchange, asset_class, frequency],
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row or row[0] is None:
            return None

        ts = row[0]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)
        return pd.to_datetime(ts, utc=True).to_pydatetime()
