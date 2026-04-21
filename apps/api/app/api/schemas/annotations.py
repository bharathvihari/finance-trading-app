from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

AnnotationType = Literal["note", "trendline", "horizontal", "vertical", "rectangle"]


class CreateAnnotationRequest(BaseModel):
    symbol: str
    exchange: str
    annotation_type: AnnotationType
    timestamp_start: datetime
    timestamp_end: datetime | None = None
    price_start: float | None = None
    price_end: float | None = None
    label: str | None = None
    color: str = "#2196F3"
    data_json: dict = {}


class UpdateAnnotationRequest(BaseModel):
    label: str | None = None
    color: str | None = None
    timestamp_start: datetime | None = None
    timestamp_end: datetime | None = None
    price_start: float | None = None
    price_end: float | None = None
    data_json: dict | None = None


class AnnotationResponse(BaseModel):
    id: UUID
    symbol: str
    exchange: str
    annotation_type: str
    timestamp_start: datetime
    timestamp_end: datetime | None
    price_start: float | None
    price_end: float | None
    label: str | None
    color: str
    data_json: dict
    created_at: datetime
    updated_at: datetime
