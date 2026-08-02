"""Derive actionable field tasks from the latest spectral indices.

Reuses the crop thresholds in ``app.services.anomaly`` to turn satellite signals
into a prioritised to-do list (irrigate / fertilise / scout). Tasks are persisted
as ``FieldTask`` rows and the generator is idempotent: it will not create a second
open task of the same type for a field that already has one pending.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import date

from geoalchemy2.functions import ST_AsText, ST_Centroid
from loguru import logger
from shapely import wkt as shapely_wkt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.index import Index
from app.services.anomaly import evaluate_field_thresholds, thresholds_for_crop
from app.services.irrigation import is_vine
from app.services.pest_catalog import PEST_CATALOG, default_pests_for_crop
from app.services.pest_model import assess_pest
from app.services.pest_risk_raster import build_pest_risk_pins
from app.services.pest_weather import degree_days_to_date, fetch_pest_weather
from app.services.prescription import base_dose_for

# Pins within this many degrees (~50 m) of an existing open pest pin are treated
# as the same hotspot — avoids re-pinning the same spot every regeneration.
_PIN_DEDUP_TOLERANCE_DEG = 0.0005


async def _latest_means(db: AsyncSession, field_id: uuid.UUID) -> dict[str, float]:
    """Return ``{index_type: latest mean_value}`` for a field."""
    out: dict[str, float] = {}
    for idx in ("NDVI", "NDMI", "NDRE"):
        r = await db.execute(
            select(Index.mean_value)
            .where(
                Index.field_id == field_id,
                Index.index_type == idx,
                Index.mean_value.isnot(None),
            )
            .order_by(Index.date.desc())
            .limit(1)
        )
        v = r.scalar_one_or_none()
        if v is not None:
            out[idx] = float(v)
    return out


def _irrigation_mm(ndmi: float, crop_type: str | None) -> int:
    """Demo irrigation quantity (mm) from the NDMI moisture deficit below the warn level."""
    warn = thresholds_for_crop(crop_type)["ndmi_warn"]
    deficit = max(0.0, warn - ndmi)
    # Scale: full deficit (~0.45) → ~35 mm; clamp to a sensible 10–35 mm band.
    return int(min(35, max(10, round(deficit / max(warn, 0.01) * 35))))


async def _has_open_task(
    db: AsyncSession, field_id: uuid.UUID, task_type: str, unpinned_only: bool = False
) -> bool:
    """``unpinned_only`` excludes located pins (e.g. pest hotspots) so a whole-field
    "inspeccion" task doesn't get silently skipped just because a pin of the same
    type already exists elsewhere on the field. Whole-field tasks now also carry
    coordinates (the centroid), so the discriminator is ``pin_scope``, not ``lat``.
    """
    conds = [
        FieldTask.field_id == field_id,
        FieldTask.task_type == task_type,
        FieldTask.status == "pendiente",
    ]
    if unpinned_only:
        conds.append(FieldTask.pin_scope == "campo")
    r = await db.execute(select(FieldTask.id).where(*conds))
    return r.first() is not None


async def _has_nearby_open_pin(db: AsyncSession, field_id: uuid.UUID, lat: float, lon: float) -> bool:
    tol = _PIN_DEDUP_TOLERANCE_DEG
    r = await db.execute(
        select(FieldTask.id).where(
            FieldTask.field_id == field_id,
            FieldTask.task_type == "inspeccion",
            FieldTask.status == "pendiente",
            # Only real hotspots dedup against a new hotspot — a whole-field task
            # pinned at the centroid must never suppress a located one near it.
            FieldTask.pin_scope == "punto",
            FieldTask.lat.isnot(None),
            FieldTask.lat.between(lat - tol, lat + tol),
            FieldTask.lon.between(lon - tol, lon + tol),
        )
    )
    return r.first() is not None


def photo_priority_for(level: str, confidence: str) -> str:
    """Active-sampling priority for asking a field photo (Fase 0, docs/ACTIVE_LEARNING.md §1).

    ``level=medio`` (decision zone) and ``level=alto`` with low/medium confidence (a
    high score resting on little real data) are the most informative cases to
    validate with a photo. A fixed 20% of low-risk cases are also sampled at random —
    without that, the dataset never gets clean negatives (never ask for photos only
    on positives).
    """
    if level == "medio":
        return "alta"
    if level == "alto":
        return "alta" if confidence in ("baja", "media") else "baja"
    return "alta" if random.random() < 0.20 else "media"


async def field_centroid(db: AsyncSession, field_id: uuid.UUID) -> tuple[float, float] | None:
    """Field centroid as (lat, lon) in WGS84, or None if the field has no geometry."""
    r = await db.execute(select(ST_AsText(ST_Centroid(Field.geometry))).where(Field.id == field_id))
    centroid_wkt = r.scalar_one_or_none()
    if not centroid_wkt:
        return None
    pt = shapely_wkt.loads(centroid_wkt)
    return round(pt.y, 5), round(((pt.x + 180) % 360) - 180, 5)


async def _pest_priority_inputs(
    db: AsyncSession, field: Field, points: list[dict]
) -> tuple[dict, float, float] | None:
    """Pest + field centroid needed for a photo-priority weather fetch, or ``None`` if not
    applicable (no pins, no catalogued pest for the crop, or the field has no geometry).
    """
    if not points:
        return None
    keys = default_pests_for_crop(field.crop_type)
    pest = PEST_CATALOG[keys[0]] if keys else None
    if not pest:
        return None
    centroid = await field_centroid(db, field.id)
    if not centroid:
        return None
    lat, lon = centroid
    return pest, lat, lon


def _pest_priority_from_weather(pest: dict, wx: dict) -> str:
    """Active-sampling photo priority (Fase 0) from a real weather reading at a field's centroid."""
    dd = (
        degree_days_to_date(wx.get("daily_means", []), pest.get("dd_base", 8))
        if pest.get("kind") == "insect"
        else None
    )
    one = assess_pest(pest, wx.get("temp"), wx.get("humidity"), wx.get("wet_hours"), dd)
    return photo_priority_for(one["level"], one["confidence"])


async def generate_pest_pins_for_field(
    db: AsyncSession,
    field: Field,
    points: list[dict] | None = None,
    priority: str | None = None,
) -> list[FieldTask]:
    """Drop map pins on pest-risk hotspots: red (alta, priority 1) / yellow (media,
    preventive, priority 3). Reuses the existing rule-based pest-risk raster —
    no ML training involved, there's no labeled pest ground-truth yet to train on.

    Each created pin also carries a non-persisted ``photo_priority`` (Active Learning
    Fase 0, see docs/ACTIVE_LEARNING.md §1), computed once per field from real weather
    against the field's primary pest, so the "confirm in field" flow can ask for
    photos where they're most informative.

    ``points``/``priority`` let a batch caller (e.g. ``generate_tasks_for_user``)
    precompute the raster query and the weather-derived priority for many fields
    concurrently and pass the results in; single-field callers can omit both and this
    falls back to computing them inline (one weather fetch, sequential).
    """
    if points is None:
        points = await build_pest_risk_pins(db, field.id, field.crop_type, settings.DATA_DIR)
    created: list[FieldTask] = []
    if not points:
        return created

    if priority is None:
        priority = "media"
        inputs = await _pest_priority_inputs(db, field, points)
        if inputs:
            pest, lat, lon = inputs
            wx = await fetch_pest_weather(lat, lon)
            priority = _pest_priority_from_weather(pest, wx)

    for p in points:
        if await _has_nearby_open_pin(db, field.id, p["lat"], p["lon"]):
            continue
        alta = p["level"] == "alta"
        t = FieldTask(
            field_id=field.id,
            task_type="inspeccion",
            title=f"{'Riesgo alto' if alta else 'Riesgo preventivo'} de {p['pest']}",
            detail=f"Zona con riesgo de {p['pest']} ({p['risk']:.0f}/100). Ir a revisar.",
            priority=1 if alta else 3,
            lat=p["lat"],
            lon=p["lon"],
            pin_scope="punto",
            due_date=date.today(),
        )
        # Ephemeral, not a mapped column (never persisted) — surfaced only in the AI
        # task-gen response. Set via __dict__ so it's a plain instance attribute
        # instead of tripping the ORM's mapped-attribute machinery.
        t.__dict__["photo_priority"] = priority
        db.add(t)
        created.append(t)
    if created:
        await db.flush()
        logger.info(f"Generated {len(created)} pest pin(s) for field {field.id}")
    return created


async def generate_tasks_for_field(
    db: AsyncSession,
    field: Field,
    pest_points: list[dict] | None = None,
    pest_priority: str | None = None,
) -> list[FieldTask]:
    """Create pending tasks for *field* from its latest indices. Returns new rows.

    ``pest_points``/``pest_priority`` are an optional precomputed pass-through to
    ``generate_pest_pins_for_field`` (see its docstring) for batch callers.
    """
    means = await _latest_means(db, field.id)
    # Pest pins have their own data check (an NDVI raster) independent of mean_value
    # being populated, so they run even when `means` is empty.
    created: list[FieldTask] = await generate_pest_pins_for_field(
        db, field, points=pest_points, priority=pest_priority
    )
    if not means:
        return created

    evals = evaluate_field_thresholds(field.crop_type, means)
    # One lookup for the whole batch: every whole-field task pins here so the worker
    # always has somewhere to walk to, marked pin_scope="campo" so the map can show
    # it as "the whole field" instead of faking a precise spot.
    centroid = await field_centroid(db, field.id)

    async def add(task_type, title, detail, priority, value=None, unpinned_only=False):
        if await _has_open_task(db, field.id, task_type, unpinned_only):
            return
        t = FieldTask(
            field_id=field.id,
            task_type=task_type,
            title=title,
            detail=detail,
            priority=priority,
            recommended_value=value,
            lat=centroid[0] if centroid else None,
            lon=centroid[1] if centroid else None,
            pin_scope="campo",
            due_date=date.today(),
        )
        db.add(t)
        created.append(t)

    # ── Riego (humedad foliar NDMI) ───────────────────────────────────────────
    ndmi_eval = evals.get("NDMI")
    if is_vine(field.crop_type):
        # Vines under regulated deficit irrigation: low NDMI is often the *target*,
        # not an emergency. Only flag genuinely severe stress, and frame it as a
        # check (the agronomist decides given the ripening stage), never "irrigate now".
        if ndmi_eval and ndmi_eval["status"] == "critical":
            await add(
                "riego",
                f"Revisar estrés hídrico en {field.name}",
                f"NDMI muy bajo ({means['NDMI']:.2f}). Si la vid NO está en maduración, considerar "
                f"riego; si está post-envero, un déficit moderado (RDI) es deseable para la calidad.",
                2,
            )
    elif ndmi_eval and ndmi_eval["status"] == "critical":
        mm = _irrigation_mm(means["NDMI"], field.crop_type)
        await add(
            "riego",
            f"Regar {field.name}",
            f"Humedad foliar crítica (NDMI {means['NDMI']:.2f}).",
            1,
            f"{mm} mm",
        )
    elif ndmi_eval and ndmi_eval["status"] == "warning":
        mm = _irrigation_mm(means["NDMI"], field.crop_type)
        await add(
            "riego",
            f"Regar {field.name} (preventivo)",
            f"Humedad foliar por debajo del umbral (NDMI {means['NDMI']:.2f}).",
            2,
            f"{mm} mm",
        )

    # ── Fertilización (clorofila NDRE) ────────────────────────────────────────
    # N dose comes from the prescription module so it matches the crop (e.g. vines
    # get ~40 kg N/ha, not a vegetable's 120) instead of a hardcoded value.
    ndre_eval = evals.get("NDRE")
    dose = base_dose_for(field.crop_type)
    if ndre_eval and ndre_eval["status"] == "critical":
        await add(
            "fertilizacion",
            f"Fertilizar {field.name}",
            f"Clorofila/nitrógeno bajo (NDRE {means['NDRE']:.2f}).",
            1,
            f"{round(dose)} kg N/ha",
        )
    elif ndre_eval and ndre_eval["status"] == "warning":
        await add(
            "fertilizacion",
            f"Revisar nutrición de {field.name}",
            f"NDRE bajo el umbral de aviso ({means['NDRE']:.2f}).",
            3,
            f"{round(dose / 2)} kg N/ha",
        )

    # ── Inspección (vegetación NDVI) ──────────────────────────────────────────
    ndvi = means.get("NDVI")
    if ndvi is not None and ndvi < 0.25:
        await add(
            "inspeccion",
            f"Inspeccionar {field.name}",
            f"Vegetación muy baja (NDVI {ndvi:.2f}). Posible estrés o daño.",
            1,
            unpinned_only=True,
        )

    if created:
        await db.flush()
        logger.info(f"Generated {len(created)} task(s) for field {field.id}")
    return created


async def generate_tasks_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Generate tasks across all of the user's fields. Returns count created."""
    r = await db.execute(select(Field).where(Field.user_id == user_id))
    fields = r.scalars().all()

    # fetch_pest_weather (Open-Meteo, up to 15s) is the only per-field network call
    # in this path. Compute the (DB-only, cheap) pest-risk points for every field
    # first, then fire all the needed weather fetches concurrently instead of
    # blocking sequentially — worst case was up to N×15s for N fields.
    points_by_field: dict[uuid.UUID, list[dict]] = {}
    weather_inputs: dict[uuid.UUID, tuple[dict, float, float]] = {}
    for f in fields:
        points = await build_pest_risk_pins(db, f.id, f.crop_type, settings.DATA_DIR)
        points_by_field[f.id] = points
        inputs = await _pest_priority_inputs(db, f, points)
        if inputs:
            weather_inputs[f.id] = inputs

    priorities: dict[uuid.UUID, str] = {}
    if weather_inputs:
        field_ids = list(weather_inputs.keys())
        results = await asyncio.gather(
            *(fetch_pest_weather(lat, lon) for _pest, lat, lon in weather_inputs.values())
        )
        for fid, wx in zip(field_ids, results, strict=True):
            pest, _lat, _lon = weather_inputs[fid]
            priorities[fid] = _pest_priority_from_weather(pest, wx)

    total = 0
    for f in fields:
        total += len(
            await generate_tasks_for_field(
                db, f, pest_points=points_by_field[f.id], pest_priority=priorities.get(f.id, "media")
            )
        )
    await db.commit()
    return total
