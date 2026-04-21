from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class CreateStrategyRequest(BaseModel):
    strategy_name: str
    mode: Literal["backtest", "paper", "live"]
    config_json: dict = Field(default_factory=dict,
                              description="NautilusTrader strategy parameters")
    description: str | None = None


class UpdateStrategyRequest(BaseModel):
    strategy_name: str | None = None
    mode: Literal["backtest", "paper", "live"] | None = None
    config_json: dict | None = None
    description: str | None = None
    is_active: bool | None = None


class StrategyResponse(BaseModel):
    id: UUID
    strategy_name: str
    mode: str
    config_json: dict
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Backtest results sub-models
# ---------------------------------------------------------------------------

class BacktestSummary(BaseModel):
    """Performance summary extracted from results_json for fast display."""
    total_return: float | None = None
    cagr: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    volatility: float | None = None
    hit_rate: float | None = None      # fraction of winning trades, e.g. 0.58
    total_trades: int | None = None


# ---------------------------------------------------------------------------
# Backtest runs
# ---------------------------------------------------------------------------

class CreateBacktestRunRequest(BaseModel):
    strategy_config_id: UUID | None = None
    portfolio_id: UUID | None = None
    params_json: dict = Field(
        default_factory=dict,
        description="Parameter snapshot for this specific run (copied from strategy_config at run time)",
    )


class SubmitBacktestResultsRequest(BaseModel):
    """
    Called by the NautilusTrader trading engine to persist backtest results.

    results_json is the full payload and must follow this structure:
    {
      "summary": {total_return, cagr, sharpe, sortino, max_drawdown, ...},
      "equity_curve":   [{"t": "<ISO datetime>", "v": <float>}, ...],
      "drawdown_curve": [{"t": "<ISO datetime>", "v": <float>}, ...],
      "trades":         [{"symbol", "side", "qty", "price", "ts", "pnl"}, ...]
    }
    """
    status: Literal["complete", "failed"]
    results_json: dict | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BacktestRunResponse(BaseModel):
    id: UUID
    strategy_config_id: UUID | None
    portfolio_id: UUID | None
    status: str
    params_json: dict
    summary: BacktestSummary | None       # extracted from results_json["summary"] if present
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class BacktestRunDetailResponse(BacktestRunResponse):
    """Full detail response — includes the raw results_json."""
    results_json: dict | None
