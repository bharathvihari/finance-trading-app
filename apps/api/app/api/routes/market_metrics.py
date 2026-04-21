"""
Pre-aggregated rolling market metrics.

Reads from market_data.rolling_metrics (populated nightly by the precompute
worker). Returns the latest computed metrics for a symbol without any
on-the-fly calculation — sub-10 ms response time for popular symbols.

Routes:
  GET /api/v1/market-data/metrics/{symbol}
      ?exchange=NASDAQ&window=252&asset_class=equity&frequency=daily
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/market-data", tags=["market-data"])

_VALID_WINDOWS = {252, 504, 756, 1260}


class RollingMetricsResponse(BaseModel):
    symbol: str
    exchange: str
    asset_class: str
    frequency: str
    window_days: int
    window_label: str          # "1Y", "2Y", "3Y", "5Y"
    as_of_date: date
    total_return: float | None
    cagr: float | None
    max_drawdown: float | None
    volatility: float | None
    sharpe: float | None
    sortino: float | None
    computed_at: str


_WINDOW_LABELS = {252: "1Y", 504: "2Y", 756: "3Y", 1260: "5Y"}


@router.get("/metrics/{symbol}", response_model=list[RollingMetricsResponse])
def get_market_metrics(
    symbol: str,
    exchange: Annotated[str, Query(description="Exchange code, e.g. NASDAQ")],
    asset_class: Annotated[str, Query()] = "equity",
    frequency: Annotated[str, Query()] = "daily",
    windows: Annotated[
        str,
        Query(description="Comma-separated window sizes in days. Valid: 252,504,756,1260"),
    ] = "252,504,756,1260",
    _: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[RollingMetricsResponse]:
    """
    Return pre-computed rolling performance metrics for a symbol.

    Data is pre-aggregated nightly (02:00 UTC) by the precompute_metrics
    ARQ task. The API reads directly from the metrics table — no computation
    at request time.

    window_days → window_label:
      252  → 1Y (trailing 1 year)
      504  → 2Y
      756  → 3Y
      1260 → 5Y
    """
    requested = [int(w.strip()) for w in windows.split(",") if w.strip().isdigit()]
    invalid = set(requested) - _VALID_WINDOWS
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid window values: {sorted(invalid)}. Valid: {sorted(_VALID_WINDOWS)}",
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, exchange, asset_class, frequency,
                   window_days, as_of_date,
                   total_return, cagr, max_drawdown, volatility, sharpe, sortino,
                   computed_at
            FROM market_data.rolling_metrics
            WHERE symbol = %s AND exchange = %s
              AND asset_class = %s AND frequency = %s
              AND window_days = ANY(%s)
              AND as_of_date = (
                  SELECT MAX(as_of_date)
                  FROM market_data.rolling_metrics
                  WHERE symbol = %s AND exchange = %s
                    AND asset_class = %s AND frequency = %s
              )
            ORDER BY window_days ASC;
            """,
            [
                symbol.upper(), exchange.upper(),
                asset_class.lower(), frequency.lower(),
                requested,
                symbol.upper(), exchange.upper(),
                asset_class.lower(), frequency.lower(),
            ],
        )
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No pre-computed metrics found for {symbol.upper()} on {exchange.upper()}. "
                "Run precompute_metrics to populate."
            ),
        )

    return [
        RollingMetricsResponse(
            symbol=r[0], exchange=r[1], asset_class=r[2], frequency=r[3],
            window_days=r[4],
            window_label=_WINDOW_LABELS.get(r[4], f"{r[4]}d"),
            as_of_date=r[5],
            total_return=r[6], cagr=r[7], max_drawdown=r[8],
            volatility=r[9], sharpe=r[10], sortino=r[11],
            computed_at=r[12].isoformat() if r[12] else "",
        )
        for r in rows
    ]
