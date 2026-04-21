from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr
    base_currency: str
    display_tz: str
    display_date_fmt: str
    created_at: datetime


class UpdateUserRequest(BaseModel):
    base_currency: str | None = None
    display_tz: str | None = None
    display_date_fmt: str | None = None
