from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas.bars import EventMarker
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/events", tags=["events"])

_TYPE_COLORS = {
    "dividend": "#4CAF50",
    "split":    "#2196F3",
    "earnings": "#FF9800",
}


@router.get("/{symbol}", response_model=list[EventMarker])
def get_events(
    symbol: str,
    exchange: Annotated[str, Query(description="Exchange code, e.g. NASDAQ")],
    asset_class: Annotated[str, Query()] = "equity",
    start: Annotated[date | None, Query(description="Inclusive start date (YYYY-MM-DD)")] = None,
    end: Annotated[date | None, Query(description="Inclusive end date (YYYY-MM-DD)")] = None,
    types: Annotated[
        str, Query(description="Comma-separated: dividend,split,earnings")
    ] = "dividend,split,earnings",
    _: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[EventMarker]:
    """
    Return corporate event markers for a symbol.

    The frontend overlays these on the candlestick chart via TradingView's
    setMarkers() API. Data is pre-populated by the fetch_events worker.
    """
    requested_types = [t.strip().lower() for t in types.split(",") if t.strip()]
    valid = {"dividend", "split", "earnings"}
    invalid = set(requested_types) - valid
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown event types: {invalid}")

    where = [
        "symbol = %s",
        "exchange = %s",
        "asset_class = %s",
        f"event_type = ANY(%s)",
    ]
    params: list[object] = [
        symbol.upper(), exchange.upper(), asset_class.lower(), requested_types,
    ]

    if start:
        where.append("event_date >= %s")
        params.append(start)
    if end:
        where.append("event_date <= %s")
        params.append(end)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT event_type, event_date, value, description
            FROM market_data.corporate_events
            WHERE {' AND '.join(where)}
            ORDER BY event_date ASC;
            """,
            params,
        )
        rows = cur.fetchall()

    markers: list[EventMarker] = []
    for event_type, event_date, value, description in rows:
        # Convert date → UTC midnight datetime for the marker timestamp
        ts = datetime(event_date.year, event_date.month, event_date.day, tzinfo=timezone.utc)
        label = description or event_type.capitalize()
        if event_type == "dividend" and value is not None:
            label = f"Div ${value:.2f}"
        elif event_type == "split" and value is not None:
            label = f"Split {value:.0f}:1"
        markers.append(EventMarker(
            t=ts,
            marker_type=event_type,
            label=label,
            color=_TYPE_COLORS.get(event_type, "#9E9E9E"),
        ))

    return markers
