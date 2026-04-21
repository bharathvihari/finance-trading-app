from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Broker accounts
# ---------------------------------------------------------------------------

class CreateBrokerAccountRequest(BaseModel):
    broker: Literal["ibkr", "paper", "demo", "manual"]
    display_name: str
    account_ref: str | None = None
    currency: str = "USD"


class UpdateBrokerAccountRequest(BaseModel):
    display_name: str | None = None
    account_ref: str | None = None
    currency: str | None = None
    is_active: bool | None = None


class BrokerAccountResponse(BaseModel):
    id: UUID
    broker: str
    display_name: str
    account_ref: str | None
    currency: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Portfolios
# ---------------------------------------------------------------------------

class CreatePortfolioRequest(BaseModel):
    name: str
    portfolio_type: Literal["live", "paper", "demo", "imported"]
    base_currency: str = "USD"
    broker_account_id: UUID | None = None
    is_default: bool = False


class UpdatePortfolioRequest(BaseModel):
    name: str | None = None
    base_currency: str | None = None
    is_default: bool | None = None


class PortfolioResponse(BaseModel):
    id: UUID
    name: str
    portfolio_type: str
    base_currency: str
    broker_account_id: UUID | None
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

class CreatePositionRequest(BaseModel):
    symbol: str
    exchange: str
    asset_class: str = "equity"
    quantity: float = Field(gt=0)
    cost_basis: float = Field(gt=0, description="Per-unit cost in portfolio base currency")
    currency: str = "USD"
    opened_at: datetime
    notes: str | None = None


class UpdatePositionRequest(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    cost_basis: float | None = Field(default=None, gt=0)
    closed_at: datetime | None = None
    notes: str | None = None


class PositionResponse(BaseModel):
    id: UUID
    symbol: str
    exchange: str
    asset_class: str
    quantity: float
    cost_basis: float
    currency: str
    opened_at: datetime
    closed_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Portfolio overview (with live prices + metrics)
# ---------------------------------------------------------------------------

class PositionWithValue(BaseModel):
    id: UUID
    symbol: str
    exchange: str
    asset_class: str
    quantity: float
    cost_basis: float          # per unit
    cost_total: float          # quantity × cost_basis
    current_price: float | None
    current_value: float | None  # quantity × current_price
    unrealized_pnl: float | None
    unrealized_pnl_pct: float | None
    currency: str
    opened_at: datetime
    closed_at: datetime | None


class PortfolioOverviewResponse(BaseModel):
    id: UUID
    name: str
    portfolio_type: str
    base_currency: str
    open_position_count: int
    total_cost: float
    total_value: float | None
    total_unrealized_pnl: float | None
    total_unrealized_pnl_pct: float | None
    positions: list[PositionWithValue]
    # Performance metrics — None when equity curve is too short
    total_return: float | None = None
    cagr: float | None = None
    max_drawdown: float | None = None
    volatility_annual: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    as_of: datetime | None = None   # timestamp of the price data used
