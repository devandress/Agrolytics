"""Field CRUD endpoints."""

import json
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from geoalchemy2.functions import ST_Area, ST_AsGeoJSON, ST_GeomFromGeoJSON, ST_Transform
from sqlalchemy import select, text

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field
from app.schemas.field import FieldCreate, FieldOut, FieldUpdate
from app.tasks.satellite_tasks import run_satellite_ingestion

router = APIRouter()


def _wrap_lon(x: float) -> float:
    """Normalise longitude into [-180, 180] (map panning can wrap it out of range)."""
    return ((x + 180) % 360) - 180


def _normalize_geometry(geo: dict) -> dict:
    """Wrap all longitudes of a GeoJSON Polygon so STAC/PostGIS see valid coords."""
    if geo and geo.get("type") == "Polygon":
        geo = {**geo, "coordinates": [[[_wrap_lon(pt[0]), pt[1]] for pt in ring]
                                      for ring in geo["coordinates"]]}
    return geo


def _wkb_to_geojson(geom_wkb) -> dict:
    """Convert GeoAlchemy2 WKBElement to a GeoJSON dict via ST_AsGeoJSON."""
    # Already serialised by the query below; this is a passthrough placeholder.
    return geom_wkb  # type: ignore[return-value]


@router.post("", response_model=FieldOut, status_code=status.HTTP_201_CREATED)
async def create_field(payload: FieldCreate, current_user: CurrentUser, db: DBSession,
                       background_tasks: BackgroundTasks) -> FieldOut:
    """Create a new field polygon for the authenticated user (auto-ingests satellite data)."""
    # Enforce the subscription plan's field limit (server-side, not just UI).
    from sqlalchemy import func

    from app.services.plans import plan_max_fields, plan_max_ha
    limit = plan_max_fields(current_user.plan)
    if limit is not None:
        used = (await db.execute(
            select(func.count(Field.id)).where(Field.user_id == current_user.id))).scalar_one()
        if used >= limit:
            raise HTTPException(
                status_code=402,
                detail=f"Tu plan permite {limit} parcela(s). Mejora tu plan para agregar más.",
            )

    geojson_str = json.dumps(_normalize_geometry(payload.geometry))

    # Calculate area in hectares using PostGIS (project to EPSG:3857 for metres)
    area_result = await db.execute(
        select(
            ST_Area(ST_Transform(ST_GeomFromGeoJSON(geojson_str), 3857)) / 10_000
        )
    )
    area_ha: float = area_result.scalar_one() or 0.0

    max_ha = plan_max_ha(current_user.plan)
    if max_ha is not None and area_ha > max_ha:
        raise HTTPException(
            status_code=402,
            detail=f"Esta parcela mide {area_ha:.1f} ha; tu plan permite hasta {max_ha} ha. "
                    "Mejora tu plan para agregarla.",
        )

    field = Field(
        user_id=current_user.id,
        name=payload.name,
        geometry=ST_GeomFromGeoJSON(geojson_str),
        area_ha=round(area_ha, 4),
        crop_type=payload.crop_type,
        notes=payload.notes,
        planting_date=payload.planting_date,
        expected_harvest=payload.expected_harvest,
        context=payload.context,
    )
    db.add(field)
    await db.commit()
    await db.refresh(field)

    # Auto-ingest: pull satellite history straight away (window driven by planting_date).
    # Runs via BackgroundTasks (always) and Celery if a worker is available.
    from app.services.satellite_ingestion import ingest_field_by_id
    background_tasks.add_task(ingest_field_by_id, str(field.id))
    try:
        run_satellite_ingestion.delay()
    except Exception:
        pass

    return await _field_with_geojson(field.id, db)


@router.get("", response_model=list[FieldOut])
async def list_fields(current_user: CurrentUser, db: DBSession) -> list[FieldOut]:
    """Return all fields owned by the authenticated user."""
    result = await db.execute(
        select(
            Field.id,
            Field.user_id,
            Field.name,
            ST_AsGeoJSON(Field.geometry).label("geometry"),
            Field.area_ha,
            Field.crop_type,
            Field.notes,
            Field.planting_date,
            Field.expected_harvest,
            Field.context,
            Field.created_at,
        ).where(Field.user_id == current_user.id)
    )
    rows = result.mappings().all()
    return [
        FieldOut(
            id=r["id"],
            user_id=r["user_id"],
            name=r["name"],
            geometry=json.loads(r["geometry"]),
            area_ha=r["area_ha"],
            crop_type=r["crop_type"],
            notes=r["notes"],
            planting_date=r["planting_date"],
            expected_harvest=r["expected_harvest"],
            context=r["context"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/{field_id}", response_model=FieldOut)
async def get_field(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> FieldOut:
    """Return a single field (must belong to authenticated user)."""
    return await _get_owned_field(field_id, current_user.id, db)


@router.patch("/{field_id}", response_model=FieldOut)
async def update_field(
    field_id: uuid.UUID, payload: FieldUpdate, current_user: CurrentUser, db: DBSession
) -> FieldOut:
    """Partially update a field's metadata or geometry."""
    result = await db.execute(
        select(Field).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    field: Field | None = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=404, detail="Field not found.")

    if payload.name is not None:
        field.name = payload.name
    if payload.crop_type is not None:
        field.crop_type = payload.crop_type
    if payload.notes is not None:
        field.notes = payload.notes
    if payload.planting_date is not None:
        field.planting_date = payload.planting_date
    if payload.expected_harvest is not None:
        field.expected_harvest = payload.expected_harvest
    if payload.context is not None:
        field.context = payload.context
    if payload.geometry is not None:
        geojson_str = json.dumps(_normalize_geometry(payload.geometry))
        area_result = await db.execute(
            select(ST_Area(ST_Transform(ST_GeomFromGeoJSON(geojson_str), 3857)) / 10_000)
        )
        new_area_ha = round(area_result.scalar_one() or 0.0, 4)

        from app.services.plans import plan_max_ha
        max_ha = plan_max_ha(current_user.plan)
        if max_ha is not None and new_area_ha > max_ha:
            raise HTTPException(
                status_code=402,
                detail=f"Esta forma mide {new_area_ha:.1f} ha; tu plan permite hasta {max_ha} ha. "
                        "Mejora tu plan para agrandarla.",
            )

        field.geometry = ST_GeomFromGeoJSON(geojson_str)
        field.area_ha = new_area_ha

    await db.commit()
    return await _field_with_geojson(field_id, db)


@router.post("/{field_id}/ingest", status_code=status.HTTP_202_ACCEPTED)
async def trigger_ingestion(
    field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
    background_tasks: BackgroundTasks,
) -> dict:
    """Dispatch a Sentinel-2 ingestion job. Runs via BackgroundTasks (always works)
    and also via Celery if a worker is available."""
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Field not found.")

    from app.services.satellite_ingestion import ingest_field_by_id
    background_tasks.add_task(ingest_field_by_id, str(field_id))

    try:
        run_satellite_ingestion.delay()
    except Exception:
        pass  # Celery not running is fine — BackgroundTasks handles it

    return {"status": "queued", "field_id": str(field_id)}


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_field(
    field_id: uuid.UUID, current_user: CurrentUser, db: DBSession
) -> None:
    """Delete a field and all associated data using raw DELETE to avoid enum deserialization errors."""
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Field not found.")
    # Delete child rows directly to bypass SQLAlchemy ORM enum loading
    await db.execute(text("DELETE FROM indices WHERE field_id = :fid"), {"fid": str(field_id)})
    await db.execute(text("DELETE FROM insights WHERE field_id = :fid"), {"fid": str(field_id)})
    await db.execute(text("DELETE FROM fields WHERE id = :fid"), {"fid": str(field_id)})
    await db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _field_with_geojson(field_id: uuid.UUID, db: DBSession) -> FieldOut:
    result = await db.execute(
        select(
            Field.id,
            Field.user_id,
            Field.name,
            ST_AsGeoJSON(Field.geometry).label("geometry"),
            Field.area_ha,
            Field.crop_type,
            Field.notes,
            Field.planting_date,
            Field.expected_harvest,
            Field.context,
            Field.created_at,
        ).where(Field.id == field_id)
    )
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found.")
    return FieldOut(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        geometry=json.loads(row["geometry"]),
        area_ha=row["area_ha"],
        crop_type=row["crop_type"],
        notes=row["notes"],
        planting_date=row["planting_date"],
        expected_harvest=row["expected_harvest"],
        context=row["context"],
        created_at=row["created_at"],
    )


async def _get_owned_field(
    field_id: uuid.UUID, user_id: uuid.UUID, db: DBSession
) -> FieldOut:
    result = await db.execute(
        select(
            Field.id,
            Field.user_id,
            Field.name,
            ST_AsGeoJSON(Field.geometry).label("geometry"),
            Field.area_ha,
            Field.crop_type,
            Field.notes,
            Field.planting_date,
            Field.expected_harvest,
            Field.context,
            Field.created_at,
        ).where(Field.id == field_id, Field.user_id == user_id)
    )
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Field not found.")
    return FieldOut(
        id=row["id"],
        user_id=row["user_id"],
        name=row["name"],
        geometry=json.loads(row["geometry"]),
        area_ha=row["area_ha"],
        crop_type=row["crop_type"],
        notes=row["notes"],
        planting_date=row["planting_date"],
        expected_harvest=row["expected_harvest"],
        context=row["context"],
        created_at=row["created_at"],
    )
