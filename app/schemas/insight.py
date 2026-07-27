"""Insight schemas."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class InsightOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    date: date
    insight_type: str
    # Full content only if purchased; otherwise a preview dict
    content: dict[str, Any] | None
    is_purchased: bool
    price_usd: float
    created_at: datetime

    model_config = {"from_attributes": True}


class InsightGenerateResponse(BaseModel):
    task_id: str
    message: str


class InsightBuyResponse(BaseModel):
    id: uuid.UUID
    is_purchased: bool
    message: str
