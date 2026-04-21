"""
Backtest run management — create, list, get results, equity curve, compare.

The trading engine (NautilusTrader) is responsible for executing the backtest
and calling PATCH /backtests/{id}/results to submit results_json.
The API layer handles storage, retrieval, and serving the curves.

Routes:
  POST   /api/v1/backtests                     create run record (status=pending)
  GET    /api/v1/backtests                     list runs for user
  GET    /api/v1/backtests/compare             overlay equity curves for multiple runs
  GET    /api/v1/backtests/{id}                get full run with results
  PATCH  /api/v1/backtests/{id}/results        submit results from trading engine
  DELETE /api/v1/backtests/{id}               delete run record
  GET    /api/v1/backtests/{id}/curve          equity + drawdown curves as ChartSeries

results_json expected structure (written by NautilusTrader adapter):
{
  "summary": {
    "total_return": 0.45, "cagr": 0.18, "sharpe": 1.23,
    "sortino": 1.45, "max_drawdown": -0.12,
    "volatility": 0.15, "hit_rate": 0.58, "total_trades": 234
  },
  "equity_curve":   [{"t": "2023-01-01T00:00:00Z", "v": 100.0}, ...],
  "drawdown_curve": [{"t": "2023-01-01T00:00:00Z", "v":   0.0}, ...],
  "trades": [
    {"symbol": "AAPL", "side": "buy", "qty": 100,
     "price": 150.0, "ts": "2023-01-05T09:30:00Z", "pnl": 520.0},
    ...
  ]
}
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extras import Json

from app.api.schemas.bars import Bar, ChartSeries
from app.api.schemas.strategies import (
    BacktestRunDetailResponse,
    BacktestRunResponse,
    BacktestSummary,
    CreateBacktestRunRequest,
    SubmitBacktestResultsRequest,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/backtests", tags=["backtests"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_summary(results_json: dict | None) -> BacktestSummary | None:
    if not results_json or "summary" not in results_json:
        return None
    s = results_json["summary"]
    return BacktestSummary(
        total_return=s.get("total_return"),
        cagr=s.get("cagr"),
        sharpe=s.get("sharpe"),
        sortino=s.get("sortino"),
        max_drawdown=s.get("max_drawdown"),
        volatility=s.get("volatility"),
        hit_rate=s.get("hit_rate"),
        total_trades=s.get("total_trades"),
    )


def _row_to_run(row: tuple, include_results: bool = False):
    (run_id, strategy_config_id, portfolio_id, run_status,
     params_json, results_json, error_message,
     started_at, finished_at, created_at) = row

    summary = _extract_summary(results_json)

    if include_results:
        return BacktestRunDetailResponse(
            id=run_id, strategy_config_id=strategy_config_id,
            portfolio_id=portfolio_id, status=run_status,
            params_json=params_json or {}, summary=summary,
            results_json=results_json,
            error_message=error_message,
            started_at=started_at, finished_at=finished_at,
            created_at=created_at,
        )
    return BacktestRunResponse(
        id=run_id, strategy_config_id=strategy_config_id,
        portfolio_id=portfolio_id, status=run_status,
        params_json=params_json or {}, summary=summary,
        error_message=error_message,
        started_at=started_at, finished_at=finished_at,
        created_at=created_at,
    )


def _get_run_or_404(conn, run_id: str, user_id: str) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, strategy_config_id, portfolio_id, status,
                   params_json, results_json, error_message,
                   started_at, finished_at, created_at
            FROM backtest_runs
            WHERE id = %s AND user_id = %s;
            """,
            [run_id, user_id],
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Backtest run not found.")
    return row


def _curve_from_results(
    results_json: dict,
    key: str,
    name: str,
    color: str,
    run_id: str,
) -> ChartSeries | None:
    """Extract a named curve from results_json and return as ChartSeries."""
    points = results_json.get(key)
    if not points:
        return None
    bars = [Bar(t=p["t"], v=float(p["v"])) for p in points if p.get("v") is not None]
    if not bars:
        return None
    return ChartSeries(
        symbol=run_id, exchange="backtest",
        asset_class="backtest", frequency="daily",
        series_type="line", name=name, color=color, bars=bars,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=BacktestRunResponse, status_code=status.HTTP_201_CREATED)
def create_backtest_run(
    body: CreateBacktestRunRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> BacktestRunResponse:
    """
    Create a backtest run record with status='pending'.

    The caller (trading engine or CLI) is responsible for picking up the
    pending job, running the backtest via NautilusTrader, and submitting
    results via PATCH /backtests/{id}/results.
    """
    run_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backtest_runs
                (id, user_id, strategy_config_id, portfolio_id, status, params_json)
            VALUES (%s, %s, %s, %s, 'pending', %s)
            RETURNING id, strategy_config_id, portfolio_id, status,
                      params_json, results_json, error_message,
                      started_at, finished_at, created_at;
            """,
            [run_id, str(current_user.id),
             str(body.strategy_config_id) if body.strategy_config_id else None,
             str(body.portfolio_id) if body.portfolio_id else None,
             Json(body.params_json)],
        )
        return _row_to_run(cur.fetchone())


@router.get("", response_model=list[BacktestRunResponse])
def list_backtest_runs(
    current_user: CurrentUser = Depends(get_current_user),
    strategy_id: Annotated[str | None, Query(description="Filter by strategy config ID")] = None,
    run_status: Annotated[str | None, Query(alias="status")] = None,
    conn=Depends(get_db),
) -> list[BacktestRunResponse]:
    """List all backtest runs for the authenticated user, newest first."""
    where = ["user_id = %s"]
    params: list[object] = [str(current_user.id)]
    if strategy_id:
        where.append("strategy_config_id = %s")
        params.append(strategy_id)
    if run_status:
        where.append("status = %s")
        params.append(run_status)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, strategy_config_id, portfolio_id, status,
                   params_json, results_json, error_message,
                   started_at, finished_at, created_at
            FROM backtest_runs
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC;
            """,
            params,
        )
        return [_row_to_run(r) for r in cur.fetchall()]


# NOTE: /compare must be declared BEFORE /{id} so FastAPI doesn't treat
# the literal string "compare" as a path-parameter value.
@router.get("/compare", response_model=list[ChartSeries])
def compare_backtests(
    ids: Annotated[str, Query(description="Comma-separated backtest run IDs")],
    normalize: Annotated[bool, Query(description="Normalize curves to 100 at start")] = True,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[ChartSeries]:
    """
    Return equity curves for multiple backtest runs as overlaid ChartSeries.

    Frontend renders them on a single chart panel for visual strategy comparison.
    Pass normalize=true (default) so curves with different starting AUM are
    all anchored to 100 for a fair side-by-side comparison.
    """
    run_ids = [rid.strip() for rid in ids.split(",") if rid.strip()]
    if not run_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Provide at least one run ID.")
    if len(run_ids) > 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Maximum 10 runs per comparison.")

    _COLORS = ["#2196F3", "#FF9800", "#4CAF50", "#E91E63",
               "#9C27B0", "#00BCD4", "#FF5722", "#795548", "#607D8B", "#F44336"]

    results: list[ChartSeries] = []
    for i, run_id in enumerate(run_ids):
        row = _get_run_or_404(conn, run_id, str(current_user.id))
        results_json: dict | None = row[5]
        if not results_json or "equity_curve" not in results_json:
            continue

        points = results_json["equity_curve"]
        bars = [Bar(t=p["t"], v=float(p["v"])) for p in points if p.get("v") is not None]
        if not bars:
            continue

        if normalize and bars:
            base = bars[0].v or 1.0
            bars = [Bar(t=b.t, v=round(b.v / base * 100, 4)) for b in bars]

        # Use strategy name from params if available.
        label = results_json.get("summary", {}).get("label") or f"Run {run_id[:8]}"
        results.append(ChartSeries(
            symbol=run_id, exchange="backtest",
            asset_class="backtest", frequency="daily",
            series_type="line", name=label,
            color=_COLORS[i % len(_COLORS)], bars=bars,
        ))

    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="None of the requested runs have equity curve data.",
        )
    return results


@router.get("/{run_id}", response_model=BacktestRunDetailResponse)
def get_backtest_run(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> BacktestRunDetailResponse:
    """Return the full backtest run record including raw results_json."""
    row = _get_run_or_404(conn, run_id, str(current_user.id))
    return _row_to_run(row, include_results=True)


@router.patch("/{run_id}/results", response_model=BacktestRunDetailResponse)
def submit_results(
    run_id: str,
    body: SubmitBacktestResultsRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> BacktestRunDetailResponse:
    """
    Called by the NautilusTrader adapter to persist backtest results.

    On success: sets status='complete', stores results_json, sets finished_at.
    On failure: sets status='failed', stores error_message.
    """
    _get_run_or_404(conn, run_id, str(current_user.id))

    updates: dict[str, object] = {"status": body.status}
    if body.results_json is not None:
        updates["results_json"] = Json(body.results_json)
    if body.error_message is not None:
        updates["error_message"] = body.error_message
    if body.started_at is not None:
        updates["started_at"] = body.started_at
    if body.finished_at is not None:
        updates["finished_at"] = body.finished_at

    set_clause = ", ".join(f"{col} = %s" for col in updates)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE backtest_runs SET {set_clause} WHERE id = %s AND user_id = %s;",
            [*updates.values(), run_id, str(current_user.id)],
        )

    return _row_to_run(_get_run_or_404(conn, run_id, str(current_user.id)),
                       include_results=True)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backtest_run(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    _get_run_or_404(conn, run_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM backtest_runs WHERE id = %s AND user_id = %s;",
            [run_id, str(current_user.id)],
        )


@router.get("/{run_id}/curve", response_model=list[ChartSeries])
def get_backtest_curve(
    run_id: str,
    normalize: Annotated[bool, Query(description="Normalize equity curve to start at 100")] = True,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[ChartSeries]:
    """
    Return the equity curve and drawdown curve for a completed backtest as ChartSeries.

    Equity curve:   series_type='line'      (portfolio value over time)
    Drawdown curve: series_type='histogram' (depth of drawdown, negative values)

    The frontend renders these in two vertically stacked chart panels —
    equity curve above, drawdown histogram below — the standard backtest dashboard layout.
    """
    row = _get_run_or_404(conn, run_id, str(current_user.id))
    results_json: dict | None = row[5]

    if not results_json:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No results available for this run yet. Status: " + str(row[3]),
        )

    output: list[ChartSeries] = []

    # Equity curve
    eq_series = _curve_from_results(
        results_json, "equity_curve", "Equity Curve", "#2196F3", run_id,
    )
    if eq_series:
        if normalize and eq_series.bars:
            base = eq_series.bars[0].v or 1.0
            eq_series.bars = [
                Bar(t=b.t, v=round(b.v / base * 100, 4)) for b in eq_series.bars
            ]
        output.append(eq_series)

    # Drawdown curve (rendered as histogram)
    dd_points = results_json.get("drawdown_curve")
    if dd_points:
        dd_bars = [Bar(t=p["t"], v=float(p["v"])) for p in dd_points if p.get("v") is not None]
        if dd_bars:
            output.append(ChartSeries(
                symbol=run_id, exchange="backtest",
                asset_class="backtest", frequency="daily",
                series_type="histogram",
                name="Drawdown", color="#F44336",
                bars=dd_bars,
            ))

    if not output:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="results_json exists but contains no curve data.",
        )
    return output
