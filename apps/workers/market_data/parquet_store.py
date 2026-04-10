from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency in lightweight envs
    duckdb = None  # type: ignore[assignment]


class ParquetStore:
    """Partitioned Parquet write/read helper for market data bars."""

    REQUIRED_COLUMNS = {"symbol", "timestamp", "open", "high", "low", "close", "volume", "asset_class", "exchange", "frequency"}

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _normalize_bars(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df.copy()

        missing = self.REQUIRED_COLUMNS.difference(df.columns)
        if missing:
            raise ValueError(f"Missing required bar columns: {sorted(missing)}")

        out = df.copy()
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        out["year"] = out["timestamp"].dt.year.astype("int32")

        for col in ("asset_class", "exchange", "frequency", "symbol"):
            out[col] = out[col].astype(str)

        out = out.sort_values(["asset_class", "exchange", "frequency", "symbol", "timestamp"])
        out = out.drop_duplicates(subset=["symbol", "exchange", "frequency", "timestamp"], keep="last")
        return out

    def _partition_dir(self, asset_class: str, exchange: str, frequency: str, year: int) -> Path:
        return (
            self.root
            / f"asset_class={asset_class}"
            / f"exchange={exchange}"
            / f"frequency={frequency}"
            / f"year={year}"
        )

    def write_partition(self, df: pd.DataFrame) -> list[Path]:
        """Write bars into hive-style yearly partitions.

        Expected directory layout:
        root/asset_class=.../exchange=.../frequency=.../year=.../part-*.parquet
        """
        normalized = self._normalize_bars(df)
        if normalized.empty:
            return []

        written: list[Path] = []
        grouped = normalized.groupby(["asset_class", "exchange", "frequency", "year"], dropna=False)

        for (asset_class, exchange, frequency, year), part_df in grouped:
            target_dir = self._partition_dir(
                asset_class=str(asset_class),
                exchange=str(exchange),
                frequency=str(frequency),
                year=int(year),
            )
            target_dir.mkdir(parents=True, exist_ok=True)

            file_name = f"part-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.parquet"
            file_path = target_dir / file_name

            # Keep partition columns in file for explicit schema consistency.
            part_df.to_parquet(file_path, index=False)
            written.append(file_path)

        return written

    def latest_timestamp(
        self,
        symbol: str,
        exchange: str,
        frequency: str,
        asset_class: str | None = None,
    ) -> datetime | None:
        """Return latest UTC timestamp for an instrument/frequency from Parquet."""
        pattern = str(self.root / "**" / "*.parquet").replace("\\", "/")
        if not list(self.root.rglob("*.parquet")):
            return None
        if duckdb is None:
            raise ModuleNotFoundError("duckdb is required for latest_timestamp queries.")

        where = [
            "symbol = ?",
            "exchange = ?",
            "frequency = ?",
        ]
        params: list[object] = [symbol, exchange, frequency]
        if asset_class is not None:
            where.append("asset_class = ?")
            params.append(asset_class)

        query = f"""
            SELECT MAX(timestamp) AS max_ts
            FROM read_parquet(?, hive_partitioning=true)
            WHERE {' AND '.join(where)};
        """

        conn = duckdb.connect(":memory:")
        try:
            row = conn.execute(query, [pattern, *params]).fetchone()
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

    def read_bars(
        self,
        symbol: str,
        exchange: str,
        frequency: str,
        asset_class: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Load bars filtered by instrument and optional time range."""
        pattern = str(self.root / "**" / "*.parquet").replace("\\", "/")
        if not list(self.root.rglob("*.parquet")):
            return pd.DataFrame()
        if duckdb is None:
            raise ModuleNotFoundError("duckdb is required for read_bars queries.")

        where = [
            "symbol = ?",
            "exchange = ?",
            "frequency = ?",
        ]
        params: list[object] = [symbol, exchange, frequency]

        if asset_class is not None:
            where.append("asset_class = ?")
            params.append(asset_class)
        if start_utc is not None:
            where.append("timestamp >= ?")
            params.append(start_utc)
        if end_utc is not None:
            where.append("timestamp <= ?")
            params.append(end_utc)

        query = f"""
            SELECT *
            FROM read_parquet(?, hive_partitioning=true)
            WHERE {' AND '.join(where)}
            ORDER BY timestamp ASC;
        """

        conn = duckdb.connect(":memory:")
        try:
            out = conn.execute(query, [pattern, *params]).df()
        finally:
            conn.close()

        if out.empty:
            return out

        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        return out
