"""Spectral index endpoints — time-series query and latest map."""

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, select

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field
from app.models.index import Index
from app.schemas.index import NDVISeriesResponse

router = APIRouter()


@router.get("/{field_id}/indices")
async def get_index_series(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    index_type: str = Query("NDVI", description="Index type: NDVI, NDMI, EVI, NDRE, LAI"),
    days: int = Query(90, ge=1, le=730),
) -> list[dict[str, Any]]:
    """Return a time-series for any spectral index over the past N days."""
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Field not found.")

    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(Index).where(
            and_(
                Index.field_id == field_id,
                Index.index_type == index_type.upper(),
                Index.date >= since,
            )
        ).order_by(Index.date.asc())
    )
    rows = result.scalars().all()
    return [
        {"date": str(r.date), "value": r.mean_value, "meta": r.extra_meta}
        for r in rows
    ]


@router.get("/{field_id}/ndvi", response_model=NDVISeriesResponse)
async def get_ndvi_series(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    start_date: date = Query(None),
    end_date: date = Query(None),
) -> NDVISeriesResponse:
    """Return NDVI time-series and the latest raster map for a field.

    - **start_date** / **end_date**: ISO date range filter (optional).
    - Returns a sorted list of ``{date, mean_value, raster_uri}`` entries and,
      when available, a GeoJSON FeatureCollection of the last acquisition.
    """
    # Verify ownership
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Field not found.")

    filters = [Index.field_id == field_id, Index.index_type == "NDVI"]
    if start_date:
        filters.append(Index.date >= start_date)
    if end_date:
        filters.append(Index.date <= end_date)

    result = await db.execute(
        select(Index).where(and_(*filters)).order_by(Index.date.asc())
    )
    rows: list[Index] = result.scalars().all()

    series: list[dict[str, Any]] = [
        {
            "date": str(r.date),
            "mean_value": r.mean_value,
            "raster_uri": r.raster_uri,
        }
        for r in rows
    ]

    # Latest GeoJSON: stored in metadata["geojson"] if present
    latest_geojson: dict[str, Any] | None = None
    if rows:
        last = rows[-1]
        if last.extra_meta and "geojson" in last.extra_meta:
            latest_geojson = last.extra_meta["geojson"]

    return NDVISeriesResponse(
        field_id=field_id,
        series=series,
        latest_geojson=latest_geojson,
    )
