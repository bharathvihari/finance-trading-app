"""
Brain↔Skin contract — unified chart/series abstraction (dashboard-requirements.md §8).

All data the API sends to the frontend travels as ChartSeries objects.
The frontend adapter maps series_type to the appropriate TradingView
(or other) API call without any backend changes.

Series-type → bar fields used:
  candlestick  →  o, h, l, c          (v is ignored; volume sent as a separate histogram series)
  line         →  v
  area         →  v
  histogram    →  v
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Bar(BaseModel):
    """Universal bar — populate only the fields relevant to series_type."""
    t: datetime
    # Single-value series (line / area / histogram / indicator output)
    v: float | None = None
    # OHLC (candlestick)
    o: float | None = None
    h: float | None = None
    l: float | None = None
    c: float | None = None


class EventMarker(BaseModel):
    """A labelled point-in-time marker overlaid on a chart (§3 event markers)."""
    t: datetime
    marker_type: Literal["dividend", "split", "earnings", "trade"]
    label: str
    color: str = "#FF9800"


class ChartSeries(BaseModel):
    """
    Framework-agnostic unified series model.

    One ChartSeries == one rendered line/candle/histogram on a chart panel.
    Multi-output indicators (MACD, Bollinger Bands) return list[ChartSeries].
    """
    symbol: str
    exchange: str
    asset_class: str
    frequency: str
    series_type: Literal["candlestick", "line", "area", "histogram"] = "candlestick"
    name: str = ""          # display label, e.g. "SMA(20)", "MACD Signal"
    color: str | None = None
    bars: list[Bar]
    markers: list[EventMarker] = []
