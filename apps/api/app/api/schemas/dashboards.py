from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class CreateWidgetRequest(BaseModel):
    widget_type: str = Field(description="e.g. 'candlestick_chart', 'portfolio_overview'")
    title: str | None = None
    config_json: dict = Field(default_factory=dict, description="Symbol, timeframe, indicators, etc.")
    position_json: dict = Field(default_factory=dict, description="{x, y, w, h} for react-grid-layout")


class UpdateWidgetRequest(BaseModel):
    title: str | None = None
    config_json: dict | None = None
    position_json: dict | None = None


class WidgetResponse(BaseModel):
    id: UUID
    dashboard_id: UUID
    widget_type: str
    title: str | None
    config_json: dict
    position_json: dict
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------

class CreateDashboardRequest(BaseModel):
    name: str
    layout_json: dict = Field(
        default_factory=dict,
        description="Global layout settings, e.g. column count, breakpoints.",
    )


class UpdateDashboardRequest(BaseModel):
    name: str | None = None
    layout_json: dict | None = None


class DashboardSummaryResponse(BaseModel):
    """Returned in list endpoints — no embedded widgets."""
    id: UUID
    name: str
    widget_count: int
    created_at: datetime
    updated_at: datetime


class DashboardDetailResponse(BaseModel):
    """Returned on GET /dashboards/{id} — includes full widget list."""
    id: UUID
    name: str
    layout_json: dict
    widgets: list[WidgetResponse]
    created_at: datetime
    updated_at: datetime
