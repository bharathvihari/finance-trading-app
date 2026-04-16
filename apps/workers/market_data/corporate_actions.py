"""Fetches stock split history from Yahoo Finance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]


@dataclass
class SplitEvent:
    symbol: str
    exchange: str
    asset_class: str
    split_date: datetime
    ratio: float  # new_shares / old_shares (e.g., 10.0 for 10:1 forward split)


def fetch_splits(
    symbol: str,
    exchange: str,
    asset_class: str,
    since: datetime | None = None,
) -> list[SplitEvent]:
    """Fetch stock split history from Yahoo Finance.

    Args:
        symbol: Ticker symbol (e.g., 'AAPL').
        exchange: Exchange name.
        asset_class: Asset class.
        since: Only return splits strictly after this date.

    Returns:
        List of SplitEvent sorted oldest-first.
    """
    if yf is None:
        raise ModuleNotFoundError(
            "yfinance is required. Install with: pip install yfinance"
        )

    ticker = yf.Ticker(symbol)
    splits = ticker.splits

    if splits is None or splits.empty:
        return []

    events: list[SplitEvent] = []
    for date, ratio in splits.items():
        if ratio == 0 or ratio == 1:
            continue
        split_dt = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
        if since is not None and split_dt <= since:
            continue
        events.append(
            SplitEvent(
                symbol=symbol,
                exchange=exchange,
                asset_class=asset_class,
                split_date=split_dt,
                ratio=float(ratio),
            )
        )

    return sorted(events, key=lambda e: e.split_date)
