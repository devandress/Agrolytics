"""Satellite raster endpoints — list available dates and render PNG overlays."""

import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from geoalchemy2.functions import ST_AsGeoJSON
from sqlalchemy import and_, select

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.models.field import Field
from app.models.index import Index

router = APIRouter()


@router.get("/{field_id}/rasters")
async def list_rasters(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    index_type: str = Query("NDVI"),
) -> list[dict]:
    """Return available dates that have raster data (real or generatable for demo)."""
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(404, "Field not found.")

    from sqlalchemy import text
    # One row per date: DISTINCT ON keeps the first row per date group.
    # Order by date DESC, raster_uri NULLS LAST so we prefer rows that have a raster file.
    result = await db.execute(
        text("""
            SELECT DISTINCT ON (date) date, mean_value, raster_uri
            FROM indices
            WHERE field_id = :fid AND index_type = :itype
            ORDER BY date DESC, raster_uri NULLS LAST
            LIMIT 90
        """),
        {"fid": str(field_id), "itype": index_type.upper()},
    )
    rows = result.all()
    return [
        {
            "date": str(r.date),
            "mean_value": r.mean_value,
            "has_real_raster": bool(r.raster_uri),
        }
        for r in rows
    ]


@router.get("/{field_id}/rasters/render")
async def render_raster(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    date_str: str = Query(..., alias="date"),
    index_type: str = Query("NDVI"),
) -> Response:
    """Return a georeferenced RGBA PNG for Leaflet imageOverlay.

    Geographic bounds are returned in the header:
      X-Raster-Bounds: west,south,east,north
    """
    from pathlib import Path

    from app.services.raster_renderer import render_to_png

    # Verify ownership & fetch field geometry
    result = await db.execute(
        select(
            Field.id,
            ST_AsGeoJSON(Field.geometry).label("geometry"),
        ).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Field not found.")

    # Parse date
    try:
        idx_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.") from None

    # Look up the Index record — multiple scenes can share the same date,
    # so take the first one (ordered to prefer records that have a raster file).
    idx_result = await db.execute(
        select(Index.raster_uri, Index.mean_value).where(
            and_(
                Index.field_id == field_id,
                Index.date == idx_date,
                Index.index_type == index_type.upper(),
            )
        )
        .order_by(Index.raster_uri.nulls_last())
        .limit(1)
    )
    idx_row = idx_result.one_or_none()
    if not idx_row:
        raise HTTPException(404, "No data for this date/type.")

    raster_uri, mean_value = idx_row

    date_tag = date_str.replace("-", "")
    expected_path = (
        Path(settings.DATA_DIR) / "cog" / str(field_id)
        / f"{index_type.upper()}_{date_tag}.tif"
    )

    if raster_uri and Path(raster_uri).exists():
        tif_path = raster_uri
    elif expected_path.exists():
        tif_path = str(expected_path)
    else:
        raise HTTPException(404, "Raster file not available yet. The satellite ingestion may still be processing.")

    png_bytes, (west, south, east, north) = render_to_png(tif_path, index_type=index_type.upper())

    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "X-Raster-Bounds": f"{west},{south},{east},{north}",
            "Cache-Control": "public, max-age=3600",
        },
    )
