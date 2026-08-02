"""Schemas for field tasks and photos (role-based views)."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator

from app.services.pest_catalog import PEST_CATALOG

_VALID_SEVERITIES = {"bajo", "medio", "alto"}
_VALID_LABEL_SOURCES = {"farmer", "agronomist", "model_assisted"}


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
    # "punto" = go to this exact spot · "campo" = whole field, pin is the centroid.
    pin_scope: str = "campo"
    due_date: date
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskWithFieldOut(TaskOut):
    """Task enriched with its field name for the cross-field daily list."""

    field_name: str | None = None
    crop_type: str | None = None


def _check_pest_key(v: str | None) -> str | None:
    """Validate a pest_key against PEST_CATALOG plus the "sano"/"otro" ground-truth values."""
    if v is not None and v not in PEST_CATALOG and v not in {"sano", "otro"}:
        raise ValueError("pest_key inválido.")
    return v


def _check_severity(v: str | None) -> str | None:
    """Validate severity against the bajo/medio/alto scale (matches the mobile mockup)."""
    if v is not None and v not in _VALID_SEVERITIES:
        raise ValueError("severity debe ser bajo, medio o alto.")
    return v


class PhotoOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    task_id: uuid.UUID | None = None
    file_path: str
    url: str | None = None
    caption: str | None = None
    alert_confirmed: bool | None = None
    pest_key: str | None = None
    severity: str | None = None
    label_source: str = "farmer"
    label_confidence: str | None = None
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PhotoLabelIn(BaseModel):
    """Farmer-in-the-loop (or admin-audited) ground-truth label for a field photo."""

    pest_key: str | None = None
    severity: str | None = None
    label_source: str = "farmer"

    @field_validator("pest_key")
    @classmethod
    def validate_pest_key(cls, v: str | None) -> str | None:
        return _check_pest_key(v)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str | None) -> str | None:
        return _check_severity(v)

    @field_validator("label_source")
    @classmethod
    def validate_label_source(cls, v: str) -> str:
        if v not in _VALID_LABEL_SOURCES:
            raise ValueError("label_source inválido.")
        return v


class PhotoReviewIn(BaseModel):
    """Agronomist/staff audit of a farmer-labelled photo (review queue)."""

    reviewed_pest_key: str | None = None
    reviewed_severity: str | None = None
    agree: bool

    @field_validator("reviewed_pest_key")
    @classmethod
    def validate_reviewed_pest_key(cls, v: str | None) -> str | None:
        return _check_pest_key(v)

    @field_validator("reviewed_severity")
    @classmethod
    def validate_reviewed_severity(cls, v: str | None) -> str | None:
        return _check_severity(v)
