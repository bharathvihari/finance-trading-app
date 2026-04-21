"""
GET /api/v1/indicators/{symbol}

Fetches OHLCV bars via BarReader, computes the requested technical indicator,
and returns one or more ChartSeries objects (multi-output indicators like MACD
and Bollinger Bands return a list with one series per output line).

Lookback buffer: the route fetches `period * 3` extra calendar days of history
before `start` so that indicator output is valid (non-NaN) from the first bar
of the requested range.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas.bars import Bar, ChartSeries
from app.auth.dependencies import CurrentUser, get_current_user
from app.lib.bar_reader import BarReader
from app.lib import indicators as ind
from app.api.routes.bars import get_bar_reader

router = APIRouter(prefix="/indicators", tags=["indicators"])

IndicatorName = Literal["SMA", "EMA", "WMA", "BB", "RSI", "MACD", "ATR"]

_INDICATOR_COLORS: dict[str, str] = {
    "SMA": "#2196F3",
    "EMA": "#9C27B0",
    "WMA": "#00BCD4",
    "BB":  "#FF5722",
    "RSI": "#4CAF50",
    "MACD": "#FF9800",
    "ATR": "#795548",
}


def _series_to_chart(
    s: pd.Series,
    *,
    symbol: str,
    exchange: str,
    asset_class: str,
    frequency: str,
    name: str,
    series_type: str = "line",
    color: str | None = None,
    start_utc: datetime | None,
) -> ChartSeries:
    """Convert a pandas Series (index=timestamp) to a ChartSeries."""
    if isinstance(s.index, pd.DatetimeIndex):
        ts_col = s.index
    else:
        ts_col = pd.to_datetime(s.index, utc=True)

    bars: list[Bar] = []
    for ts, val in zip(ts_col, s):
        if start_utc is not None and ts < start_utc:
            continue
        if pd.isna(val):
            continue
        bars.append(Bar(t=ts, v=float(val)))

    return ChartSeries(
        symbol=symbol, exchange=exchange, asset_class=asset_class,
        frequency=frequency, series_type=series_type,  # type: ignore[arg-type]
        name=name, color=color, bars=bars,
    )


@router.get("/{symbol}", response_model=list[ChartSeries])
def get_indicator(
    symbol: str,
    exchange: Annotated[str, Query(description="Exchange code, e.g. NASDAQ")],
    indicator: Annotated[IndicatorName, Query(description="Indicator name")],
    asset_class: Annotated[str, Query()] = "equity",
    frequency: Annotated[str, Query()] = "daily",
    period: Annotated[int, Query(ge=1, le=500)] = 20,
    # MACD-specific
    fast: Annotated[int, Query(ge=1)] = 12,
    slow: Annotated[int, Query(ge=1)] = 26,
    signal: Annotated[int, Query(ge=1)] = 9,
    # Bollinger Bands-specific
    std_dev: Annotated[float, Query(ge=0.1, le=10.0)] = 2.0,
    start: Annotated[date | None, Query(description="Inclusive start date (YYYY-MM-DD)")] = None,
    end: Annotated[date | None, Query(description="Inclusive end date (YYYY-MM-DD)")] = None,
    reader: BarReader = Depends(get_bar_reader),
    _: CurrentUser = Depends(get_current_user),
) -> list[ChartSeries]:
    """
    Compute a technical indicator for one instrument.

    Multi-output indicators (BB, MACD) return one ChartSeries per output line.
    Single-output indicators (SMA, EMA, WMA, RSI, ATR) return a single-item list.
    """
    start_utc: datetime | None = (
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc) if start else None
    )
    end_utc: datetime | None = (
        datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc) if end else None
    )

    # Fetch extra history so the indicator warms up before `start`.
    lookback_days = max(period, slow) * 3
    fetch_start = (start_utc - timedelta(days=lookback_days)) if start_utc else None

    df = reader.read(
        symbol=symbol.upper(),
        exchange=exchange.upper(),
        asset_class=asset_class.lower(),
        frequency=frequency.lower(),
        start_utc=fetch_start,
        end_utc=end_utc,
    )

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No bar data found for {symbol.upper()} on {exchange.upper()}.",
        )

    df = df.set_index("timestamp").sort_index()
    close = df["close"]
    color = _INDICATOR_COLORS.get(indicator)
    sym = symbol.upper()
    exch = exchange.upper()
    ac = asset_class.lower()
    freq = frequency.lower()

    kwargs = dict(symbol=sym, exchange=exch, asset_class=ac, frequency=freq, start_utc=start_utc)

    # ------------------------------------------------------------------
    # Single-output indicators
    # ------------------------------------------------------------------
    if indicator == "SMA":
        s = ind.sma(close, period)
        return [_series_to_chart(s, name=f"SMA({period})", color=color, **kwargs)]

    if indicator == "EMA":
        s = ind.ema(close, period)
        return [_series_to_chart(s, name=f"EMA({period})", color=color, **kwargs)]

    if indicator == "WMA":
        s = ind.wma(close, period)
        return [_series_to_chart(s, name=f"WMA({period})", color=color, **kwargs)]

    if indicator == "RSI":
        s = ind.rsi(close, period)
        return [_series_to_chart(s, name=f"RSI({period})", color=color, **kwargs)]

    if indicator == "ATR":
        s = ind.atr(df["high"], df["low"], close, period)
        return [_series_to_chart(s, name=f"ATR({period})", color=color, **kwargs)]

    # ------------------------------------------------------------------
    # Multi-output indicators
    # ------------------------------------------------------------------
    if indicator == "BB":
        bb = ind.bollinger_bands(close, period=period, std_dev=std_dev)
        colors = {"bb_upper": "#FF5722", "bb_middle": "#FF9800", "bb_lower": "#FF5722"}
        names  = {
            "bb_upper":  f"BB Upper({period}, {std_dev})",
            "bb_middle": f"BB Middle({period})",
            "bb_lower":  f"BB Lower({period}, {std_dev})",
        }
        return [
            _series_to_chart(bb[col], name=names[col], color=colors[col], **kwargs)
            for col in ("bb_upper", "bb_middle", "bb_lower")
        ]

    if indicator == "MACD":
        mc = ind.macd(close, fast=fast, slow=slow, signal=signal)
        configs = [
            ("macd",           f"MACD({fast},{slow})",       "#2196F3", "line"),
            ("macd_signal",    f"Signal({signal})",           "#FF5722", "line"),
            ("macd_histogram", f"Histogram({fast},{slow})",   "#4CAF50", "histogram"),
        ]
        return [
            _series_to_chart(mc[col], name=name, color=clr, series_type=stype, **kwargs)
            for col, name, clr, stype in configs
        ]

    # Should be unreachable due to Literal type constraint
    raise HTTPException(status_code=400, detail=f"Unknown indicator: {indicator}")
