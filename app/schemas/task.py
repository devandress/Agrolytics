"""Schemas for field tasks and photos (role-based views)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class TaskOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    task_type: str
    title: str
    detail: str | None = None
    priority: int
    status: str
    recommended_value: str | None = None
    zone: str | None = None
    lat: float | None = None
    lon: float | None = None
    due_date: date
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskWithFieldOut(TaskOut):
    """Task enriched with its field name for the cross-field daily list."""
    field_name: str | None = None
    crop_type: str | None = None


class PhotoOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    task_id: uuid.UUID | None = None
    file_path: str
    url: str | None = None
    caption: str | None = None
    alert_confirmed: bool | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
