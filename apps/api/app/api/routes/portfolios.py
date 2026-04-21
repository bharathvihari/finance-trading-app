"""
Portfolio CRUD, Position CRUD, Portfolio Overview, and Equity Curve.

Route structure:
  /api/v1/portfolios
    POST   /                        create portfolio
    GET    /                        list portfolios
    GET    /{id}                    get portfolio
    PATCH  /{id}                    update portfolio
    DELETE /{id}                    delete portfolio
    POST   /{id}/positions          add position
    GET    /{id}/positions          list positions (open + closed)
    PATCH  /{id}/positions/{pos_id} update position (e.g. close it)
    DELETE /{id}/positions/{pos_id} delete position
    GET    /{id}/overview           current prices + unrealized P&L + metrics
    GET    /{id}/curve              equity curve as ChartSeries list
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.schemas.bars import Bar, ChartSeries
from app.api.schemas.portfolios import (
    CreatePortfolioRequest,
    CreatePositionRequest,
    PortfolioOverviewResponse,
    PortfolioResponse,
    PositionResponse,
    PositionWithValue,
    UpdatePortfolioRequest,
    UpdatePositionRequest,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db
from app.lib import metrics as m
from app.lib.bar_reader import BarReader
from app.api.routes.bars import get_bar_reader

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_portfolio_or_404(conn, portfolio_id: str, user_id: str) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, broker_account_id, name, portfolio_type,
                   base_currency, is_default, created_at, updated_at
            FROM portfolios
            WHERE id = %s AND user_id = %s;
            """,
            [portfolio_id, user_id],
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Portfolio not found.")
    return {
        "id": row[0], "user_id": row[1], "broker_account_id": row[2],
        "name": row[3], "portfolio_type": row[4], "base_currency": row[5],
        "is_default": row[6], "created_at": row[7], "updated_at": row[8],
    }


def _row_to_portfolio(row: tuple) -> PortfolioResponse:
    return PortfolioResponse(
        id=row[0], broker_account_id=row[2], name=row[3],
        portfolio_type=row[4], base_currency=row[5],
        is_default=row[6], created_at=row[7], updated_at=row[8],
    )


def _row_to_position(row: tuple) -> PositionResponse:
    return PositionResponse(
        id=row[0], symbol=row[1], exchange=row[2], asset_class=row[3],
        quantity=row[4], cost_basis=row[5], currency=row[6],
        opened_at=row[7], closed_at=row[8], notes=row[9],
        created_at=row[10], updated_at=row[11],
    )


def _fetch_open_positions(conn, portfolio_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, exchange, asset_class,
                   quantity, cost_basis, currency,
                   opened_at, closed_at, notes, created_at, updated_at
            FROM positions
            WHERE portfolio_id = %s AND closed_at IS NULL
            ORDER BY opened_at ASC;
            """,
            [portfolio_id],
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Portfolio CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
def create_portfolio(
    body: CreatePortfolioRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> PortfolioResponse:
    """Create a new portfolio (live, paper, demo, or imported)."""
    port_id = str(uuid.uuid4())

    # If marked as default, clear existing default first.
    if body.is_default:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE portfolios SET is_default = FALSE WHERE user_id = %s;",
                [str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolios
                (id, user_id, broker_account_id, name, portfolio_type,
                 base_currency, is_default)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, user_id, broker_account_id, name, portfolio_type,
                      base_currency, is_default, created_at, updated_at;
            """,
            [port_id, str(current_user.id),
             str(body.broker_account_id) if body.broker_account_id else None,
             body.name, body.portfolio_type, body.base_currency, body.is_default],
        )
        return _row_to_portfolio(cur.fetchone())


@router.get("", response_model=list[PortfolioResponse])
def list_portfolios(
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[PortfolioResponse]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, broker_account_id, name, portfolio_type,
                   base_currency, is_default, created_at, updated_at
            FROM portfolios WHERE user_id = %s
            ORDER BY is_default DESC, created_at ASC;
            """,
            [str(current_user.id)],
        )
        return [_row_to_portfolio(r) for r in cur.fetchall()]


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
def get_portfolio(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> PortfolioResponse:
    p = _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    return PortfolioResponse(**{k: v for k, v in p.items() if k != "user_id"})


@router.patch("/{portfolio_id}", response_model=PortfolioResponse)
def update_portfolio(
    portfolio_id: str,
    body: UpdatePortfolioRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> PortfolioResponse:
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))

    if body.is_default:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE portfolios SET is_default = FALSE WHERE user_id = %s;",
                [str(current_user.id)],
            )

    updates: dict[str, object] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.base_currency is not None:
        updates["base_currency"] = body.base_currency
    if body.is_default is not None:
        updates["is_default"] = body.is_default

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE portfolios SET {set_clause} WHERE id = %s AND user_id = %s;",
                [*updates.values(), portfolio_id, str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, broker_account_id, name, portfolio_type,
                   base_currency, is_default, created_at, updated_at
            FROM portfolios WHERE id = %s;
            """,
            [portfolio_id],
        )
        return _row_to_portfolio(cur.fetchone())


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM portfolios WHERE id = %s;", [portfolio_id]
        )


# ---------------------------------------------------------------------------
# Position CRUD
# ---------------------------------------------------------------------------

@router.post(
    "/{portfolio_id}/positions",
    response_model=PositionResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_position(
    portfolio_id: str,
    body: CreatePositionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> PositionResponse:
    """Add a position (buy lot) to a portfolio."""
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    pos_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO positions
                (id, portfolio_id, user_id, symbol, exchange, asset_class,
                 quantity, cost_basis, currency, opened_at, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, symbol, exchange, asset_class, quantity, cost_basis,
                      currency, opened_at, closed_at, notes, created_at, updated_at;
            """,
            [pos_id, portfolio_id, str(current_user.id),
             body.symbol.upper(), body.exchange.upper(), body.asset_class.lower(),
             body.quantity, body.cost_basis, body.currency,
             body.opened_at, body.notes],
        )
        return _row_to_position(cur.fetchone())


@router.get("/{portfolio_id}/positions", response_model=list[PositionResponse])
def list_positions(
    portfolio_id: str,
    open_only: Annotated[bool, Query(description="Return only open positions")] = False,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[PositionResponse]:
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    closed_filter = "AND closed_at IS NULL" if open_only else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, symbol, exchange, asset_class, quantity, cost_basis,
                   currency, opened_at, closed_at, notes, created_at, updated_at
            FROM positions
            WHERE portfolio_id = %s {closed_filter}
            ORDER BY opened_at ASC;
            """,
            [portfolio_id],
        )
        return [_row_to_position(r) for r in cur.fetchall()]


@router.patch("/{portfolio_id}/positions/{position_id}", response_model=PositionResponse)
def update_position(
    portfolio_id: str,
    position_id: str,
    body: UpdatePositionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> PositionResponse:
    """Update a position — commonly used to close it (set closed_at)."""
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))

    updates: dict[str, object] = {}
    if body.quantity is not None:
        updates["quantity"] = body.quantity
    if body.cost_basis is not None:
        updates["cost_basis"] = body.cost_basis
    if body.closed_at is not None:
        updates["closed_at"] = body.closed_at
    if body.notes is not None:
        updates["notes"] = body.notes

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE positions SET {set_clause}
                WHERE id = %s AND portfolio_id = %s AND user_id = %s;
                """,
                [*updates.values(), position_id, portfolio_id, str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, exchange, asset_class, quantity, cost_basis,
                   currency, opened_at, closed_at, notes, created_at, updated_at
            FROM positions WHERE id = %s AND portfolio_id = %s;
            """,
            [position_id, portfolio_id],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found.")
    return _row_to_position(row)


@router.delete("/{portfolio_id}/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_position(
    portfolio_id: str,
    position_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM positions WHERE id = %s AND portfolio_id = %s AND user_id = %s;",
            [position_id, portfolio_id, str(current_user.id)],
        )


# ---------------------------------------------------------------------------
# Portfolio overview — live prices + unrealized P&L + metrics
# ---------------------------------------------------------------------------

@router.get("/{portfolio_id}/overview", response_model=PortfolioOverviewResponse)
def get_overview(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
    reader: BarReader = Depends(get_bar_reader),
) -> PortfolioOverviewResponse:
    """
    Return open positions enriched with their latest market price,
    unrealized P&L, and aggregate performance metrics computed from
    the historical equity curve.
    """
    port = _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    raw_positions = _fetch_open_positions(conn, portfolio_id)

    positions_with_value: list[PositionWithValue] = []
    total_cost = 0.0
    total_value = 0.0
    has_prices = False
    as_of: datetime | None = None

    for row in raw_positions:
        pos_id, symbol, exchange, asset_class, qty, cost_basis, currency, \
            opened_at, closed_at, notes, created_at, updated_at = row

        cost_total = qty * cost_basis
        total_cost += cost_total

        price = reader.latest_price(symbol, exchange, asset_class)
        current_value = qty * price if price is not None else None
        unreal_pnl = (current_value - cost_total) if current_value is not None else None
        unreal_pnl_pct = (unreal_pnl / cost_total) if unreal_pnl is not None and cost_total else None

        if price is not None:
            has_prices = True
            total_value += current_value
            as_of = datetime.now(timezone.utc)

        positions_with_value.append(PositionWithValue(
            id=pos_id, symbol=symbol, exchange=exchange, asset_class=asset_class,
            quantity=qty, cost_basis=cost_basis, cost_total=cost_total,
            current_price=price, current_value=current_value,
            unrealized_pnl=unreal_pnl, unrealized_pnl_pct=unreal_pnl_pct,
            currency=currency, opened_at=opened_at, closed_at=closed_at,
        ))

    total_unreal_pnl = (total_value - total_cost) if has_prices else None
    total_unreal_pct = (total_unreal_pnl / total_cost) if (total_unreal_pnl is not None and total_cost) else None

    # Compute performance metrics from the equity curve (if enough history).
    perf: dict = {}
    if raw_positions:
        pos_dicts = [
            {"symbol": r[1], "exchange": r[2], "asset_class": r[3], "quantity": r[4]}
            for r in raw_positions
        ]
        curve = m.build_portfolio_curve(pos_dicts, reader)
        if len(curve) >= 30:
            perf = m.compute_metrics(curve)

    return PortfolioOverviewResponse(
        id=port["id"], name=port["name"],
        portfolio_type=port["portfolio_type"],
        base_currency=port["base_currency"],
        open_position_count=len(raw_positions),
        total_cost=total_cost,
        total_value=total_value if has_prices else None,
        total_unrealized_pnl=total_unreal_pnl,
        total_unrealized_pnl_pct=total_unreal_pct,
        positions=positions_with_value,
        total_return=perf.get("total_return"),
        cagr=perf.get("cagr"),
        max_drawdown=perf.get("max_drawdown"),
        volatility_annual=perf.get("volatility"),
        sharpe=perf.get("sharpe"),
        sortino=perf.get("sortino"),
        as_of=as_of,
    )


# ---------------------------------------------------------------------------
# Equity curve — historical portfolio value + optional benchmark overlay
# ---------------------------------------------------------------------------

@router.get("/{portfolio_id}/curve", response_model=list[ChartSeries])
def get_equity_curve(
    portfolio_id: str,
    start: Annotated[date | None, Query(description="Start date (YYYY-MM-DD)")] = None,
    end: Annotated[date | None, Query(description="End date (YYYY-MM-DD)")] = None,
    benchmark: Annotated[str | None, Query(description="Benchmark symbol e.g. SPX")] = None,
    benchmark_exchange: Annotated[str, Query()] = "NYSE",
    benchmark_asset_class: Annotated[str, Query()] = "equity",
    normalize: Annotated[bool, Query(description="Normalize both curves to 100 at start")] = True,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
    reader: BarReader = Depends(get_bar_reader),
) -> list[ChartSeries]:
    """
    Return the portfolio equity curve as a line ChartSeries.

    Optionally overlay a benchmark index (e.g. SPX, NDX) normalized to the
    same starting value so performance can be compared visually.

    The curve is the sum of (quantity × daily_close) for all open positions,
    joined on trading date. Forward-fill is applied when a position's symbol
    has no bar on a given date (e.g. different exchange holiday calendars).
    """
    port = _get_portfolio_or_404(conn, portfolio_id, str(current_user.id))
    raw_positions = _fetch_open_positions(conn, portfolio_id)

    if not raw_positions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Portfolio has no open positions — cannot build equity curve.",
        )

    start_utc = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) if start else None
    end_utc = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc) if end else None

    pos_dicts = [
        {"symbol": r[1], "exchange": r[2], "asset_class": r[3], "quantity": r[4]}
        for r in raw_positions
    ]

    curve = m.build_portfolio_curve(pos_dicts, reader, start_utc, end_utc)

    if curve.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No price data available to build the equity curve.",
        )

    if normalize:
        curve = m.equity_curve(curve)

    portfolio_bars = [
        Bar(t=ts, v=round(float(val), 4))
        for ts, val in curve.items()
    ]

    result: list[ChartSeries] = [
        ChartSeries(
            symbol=portfolio_id,
            exchange="portfolio",
            asset_class="portfolio",
            frequency="daily",
            series_type="line",
            name=port["name"],
            color="#2196F3",
            bars=portfolio_bars,
        )
    ]

    # Benchmark overlay
    if benchmark:
        bm_df = reader.read(
            symbol=benchmark.upper(),
            exchange=benchmark_exchange.upper(),
            asset_class=benchmark_asset_class.lower(),
            frequency="daily",
            start_utc=start_utc,
            end_utc=end_utc,
        )
        if not bm_df.empty:
            import pandas as pd
            bm_series = bm_df.set_index("timestamp")["close"].sort_index()
            if normalize:
                bm_series = m.equity_curve(bm_series)
            result.append(ChartSeries(
                symbol=benchmark.upper(),
                exchange=benchmark_exchange.upper(),
                asset_class=benchmark_asset_class.lower(),
                frequency="daily",
                series_type="line",
                name=benchmark.upper(),
                color="#FF9800",
                bars=[Bar(t=ts, v=round(float(v), 4)) for ts, v in bm_series.items()],
            ))

    return result
