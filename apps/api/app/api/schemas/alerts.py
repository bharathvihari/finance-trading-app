from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AlertConditionType = Literal[
    "price_above", "price_below",
    "pct_change_above", "pct_change_below",
]


class CreateAlertRequest(BaseModel):
    symbol: str
    exchange: str
    alert_type: str = Field(default="price", description="Category label, e.g. 'price', 'volume'")
    message: str = Field(description="Human-readable description shown when triggered")
    condition_json: dict = Field(
        description=(
            "Condition definition. Examples:\n"
            '  {"type": "price_above",  "threshold": 200.0}\n'
            '  {"type": "price_below",  "threshold": 150.0}\n'
            '  {"type": "pct_change_above", "threshold": 0.05, "reference_price": 180.0}'
        )
    )


class UpdateAlertRequest(BaseModel):
    message: str | None = None
    condition_json: dict | None = None
    status: Literal["active", "dismissed"] | None = None


class AlertResponse(BaseModel):
    id: UUID
    symbol: str
    exchange: str
    alert_type: str
    message: str
    condition_json: dict
    status: str
    triggered_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
