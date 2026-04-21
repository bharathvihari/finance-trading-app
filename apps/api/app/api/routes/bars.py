from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from fastapi import Request

from app.api.schemas.bars import Bar, ChartSeries
from app.auth.dependencies import CurrentUser, get_current_user
from app.core.config import Settings, settings as _default_settings
from app.lib.bar_reader import BarReader

router = APIRouter(prefix="/bars", tags=["bars"])


def _reader_from_settings(s: Settings, cache=None) -> BarReader:
    return BarReader(
        parquet_root=s.parquet_root,
        hot_window_months=s.hot_window_months,
        postgres_enabled=s.postgres_enabled,
        postgres_host=s.postgres_host,
        postgres_port=s.postgres_port,
        postgres_db=s.postgres_db,
        postgres_user=s.postgres_user,
        postgres_password=s.postgres_password,
        postgres_schema=s.postgres_schema,
        postgres_bars_table=s.postgres_bars_table,
        cache=cache,
    )


def get_bar_reader(request: Request) -> BarReader:
    cache = getattr(request.app.state, "bar_cache", None)
    return _reader_from_settings(_default_settings, cache=cache)


@router.get("/{symbol}", response_model=ChartSeries)
def get_bars(
    symbol: str,
    exchange: Annotated[str, Query(description="Exchange code, e.g. NASDAQ")],
    asset_class: Annotated[str, Query()] = "equity",
    frequency: Annotated[str, Query()] = "daily",
    start: Annotated[date | None, Query(description="Inclusive start date (YYYY-MM-DD)")] = None,
    end: Annotated[date | None, Query(description="Inclusive end date (YYYY-MM-DD)")] = None,
    reader: BarReader = Depends(get_bar_reader),
    _: CurrentUser = Depends(get_current_user),
) -> ChartSeries:
    """
    Return OHLCV bars for one instrument over the requested date range.

    Data is served from the hot tier (Postgres, recent months) and/or the
    cold tier (Parquet, historical) and merged transparently.
    """
    start_utc: datetime | None = (
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc) if start else None
    )
    end_utc: datetime | None = (
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc) if end else None
    )

    df = reader.read(
        symbol=symbol.upper(),
        exchange=exchange.upper(),
        asset_class=asset_class.lower(),
        frequency=frequency.lower(),
        start_utc=start_utc,
        end_utc=end_utc,
    )

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No bar data found for {symbol.upper()} on {exchange.upper()}.",
        )

    bars = [
        Bar(t=row.timestamp, o=row.open, h=row.high, l=row.low, c=row.close)
        for row in df.itertuples(index=False)
    ]

    return ChartSeries(
        symbol=symbol.upper(),
        exchange=exchange.upper(),
        asset_class=asset_class.lower(),
        frequency=frequency.lower(),
        name=symbol.upper(),
        series_type="candlestick",
        bars=bars,
    )
