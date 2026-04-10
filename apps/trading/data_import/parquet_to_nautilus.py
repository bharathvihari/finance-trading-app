from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Any

import pandas as pd


class NautilusImportError(Exception):
    """Raised when parquet -> nautilus conversion cannot be completed."""


_COLUMN_ALIASES = {
    "timestamp": ["timestamp", "ts", "time"],
    "open": ["open", "o"],
    "high": ["high", "h"],
    "low": ["low", "l"],
    "close": ["close", "c"],
    "volume": ["volume", "v"],
}


def _resolve_column(df: pd.DataFrame, canonical: str) -> str:
    for name in _COLUMN_ALIASES[canonical]:
        if name in df.columns:
            return name
    raise NautilusImportError(f"Missing required column for '{canonical}' in parquet dataset.")


def _normalize_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    ts_col = _resolve_column(df, "timestamp")
    out = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(df[ts_col], utc=True, errors="coerce"),
            "open": pd.to_numeric(df[_resolve_column(df, "open")], errors="coerce"),
            "high": pd.to_numeric(df[_resolve_column(df, "high")], errors="coerce"),
            "low": pd.to_numeric(df[_resolve_column(df, "low")], errors="coerce"),
            "close": pd.to_numeric(df[_resolve_column(df, "close")], errors="coerce"),
            "volume": pd.to_numeric(df[_resolve_column(df, "volume")], errors="coerce"),
        }
    )
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
    out = out.sort_values("timestamp")
    out = out.drop_duplicates(subset=["timestamp"], keep="last")
    return out


def load_daily_parquet_bars(
    parquet_root: str | Path,
    symbol: str,
    exchange: str,
    asset_class: str = "equity",
    frequency: str = "daily",
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> pd.DataFrame:
    """Load daily bars from partitioned parquet dataset for one symbol."""
    root = Path(parquet_root)
    base = (
        root
        / f"asset_class={asset_class}"
        / f"exchange={exchange}"
        / f"frequency={frequency}"
    )
    files = sorted(base.glob("year=*/part-*.parquet"))
    if not files:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    frames: list[pd.DataFrame] = []
    for file in files:
        frame = pd.read_parquet(file)
        if "symbol" in frame.columns:
            frame = frame[frame["symbol"] == symbol]
        if start_utc is not None and "timestamp" in frame.columns:
            frame = frame[pd.to_datetime(frame["timestamp"], utc=True, errors="coerce") >= pd.Timestamp(start_utc)]
        if end_utc is not None and "timestamp" in frame.columns:
            frame = frame[pd.to_datetime(frame["timestamp"], utc=True, errors="coerce") <= pd.Timestamp(end_utc)]
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return _normalize_daily_frame(pd.concat(frames, ignore_index=True))


def to_nautilus_payloads(df: pd.DataFrame, instrument_id: str, bar_type: str) -> list[dict[str, Any]]:
    """Convert normalized daily bars into Nautilus-ready payload dictionaries."""
    normalized = _normalize_daily_frame(df)
    payloads: list[dict[str, Any]] = []
    for row in normalized.itertuples(index=False):
        payloads.append(
            {
                "instrument_id": instrument_id,
                "bar_type": bar_type,
                "ts_event": row.timestamp,
                "ts_init": row.timestamp,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
        )
    return payloads


def to_nautilus_bar_objects(
    df: pd.DataFrame,
    instrument_id: str,
    bar_type: str,
    bar_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> list[Any]:
    """Convert bars into Nautilus Bar objects via provided factory.

    Provide `bar_factory` from your Nautilus runtime integration layer.
    This keeps this module stable across Nautilus versions.
    """
    payloads = to_nautilus_payloads(df=df, instrument_id=instrument_id, bar_type=bar_type)
    if bar_factory is None:
        raise NautilusImportError(
            "No bar_factory provided. Pass a callable that builds Nautilus Bar objects from payload dicts."
        )
    return [bar_factory(payload) for payload in payloads]
