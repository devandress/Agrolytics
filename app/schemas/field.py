"""Field request / response schemas.

Geometries are represented as GeoJSON-compatible dicts (type + coordinates).
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, model_validator


class FieldCreate(BaseModel):
    name: str
    geometry: dict[str, Any]  # GeoJSON Polygon
    crop_type: str | None = None
    notes: str | None = None
    planting_date: date | None = None
    expected_harvest: date | None = None
    context: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_geometry_type(self) -> "FieldCreate":
        if self.geometry.get("type") != "Polygon":
            raise ValueError("geometry must be a GeoJSON Polygon.")
        return self


class FieldUpdate(BaseModel):
    name: str | None = None
    geometry: dict[str, Any] | None = None
    crop_type: str | None = None
    notes: str | None = None
    planting_date: date | None = None
    expected_harvest: date | None = None
    context: dict[str, Any] | None = None


class FieldOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    geometry: dict[str, Any]
    area_ha: float
    crop_type: str | None
    notes: str | None
    planting_date: date | None = None
    expected_harvest: date | None = None
    context: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
