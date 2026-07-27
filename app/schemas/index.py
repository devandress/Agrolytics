"""Spectral index schemas."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class IndexOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    date: date
    index_type: str
    raster_uri: str | None
    mean_value: float | None
    extra_meta: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class NDVISeriesResponse(BaseModel):
    """Time-series + latest GeoJSON returned by GET /fields/{id}/ndvi."""

    field_id: uuid.UUID
    series: list[dict[str, Any]]   # [{date, mean_value, raster_uri}, ...]
    latest_geojson: dict[str, Any] | None  # last GeoJSON FeatureCollection or None
