"""Derive actionable field tasks from the latest spectral indices.

Reuses the crop thresholds in ``app.services.anomaly`` to turn satellite signals
into a prioritised to-do list (irrigate / fertilise / scout). Tasks are persisted
as ``FieldTask`` rows and the generator is idempotent: it will not create a second
open task of the same type for a field that already has one pending.
"""

from __future__ import annotations

import uuid
from datetime import date

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.index import Index
from app.services.anomaly import evaluate_field_thresholds, thresholds_for_crop
from app.services.irrigation import is_vine
from app.services.pest_risk_raster import build_pest_risk_pins
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
    type already exists elsewhere on the field.
    """
    conds = [
        FieldTask.field_id == field_id,
        FieldTask.task_type == task_type,
        FieldTask.status == "pendiente",
    ]
    if unpinned_only:
        conds.append(FieldTask.lat.is_(None))
    r = await db.execute(select(FieldTask.id).where(*conds))
    return r.first() is not None


async def _has_nearby_open_pin(db: AsyncSession, field_id: uuid.UUID, lat: float, lon: float) -> bool:
    tol = _PIN_DEDUP_TOLERANCE_DEG
    r = await db.execute(
        select(FieldTask.id).where(
            FieldTask.field_id == field_id,
            FieldTask.task_type == "inspeccion",
            FieldTask.status == "pendiente",
            FieldTask.lat.isnot(None),
            FieldTask.lat.between(lat - tol, lat + tol),
            FieldTask.lon.between(lon - tol, lon + tol),
        )
    )
    return r.first() is not None


async def generate_pest_pins_for_field(db: AsyncSession, field: Field) -> list[FieldTask]:
    """Drop map pins on pest-risk hotspots: red (alta, priority 1) / yellow (media,
    preventive, priority 3). Reuses the existing rule-based pest-risk raster —
    no ML training involved, there's no labeled pest ground-truth yet to train on.
    """
    points = await build_pest_risk_pins(db, field.id, field.crop_type, settings.DATA_DIR)
    created: list[FieldTask] = []
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
            lat=p["lat"], lon=p["lon"],
            due_date=date.today(),
        )
        db.add(t)
        created.append(t)
    if created:
        await db.flush()
        logger.info(f"Generated {len(created)} pest pin(s) for field {field.id}")
    return created


async def generate_tasks_for_field(db: AsyncSession, field: Field) -> list[FieldTask]:
    """Create pending tasks for *field* from its latest indices. Returns new rows."""
    means = await _latest_means(db, field.id)
    # Pest pins have their own data check (an NDVI raster) independent of mean_value
    # being populated, so they run even when `means` is empty.
    created: list[FieldTask] = await generate_pest_pins_for_field(db, field)
    if not means:
        return created

    evals = evaluate_field_thresholds(field.crop_type, means)

    async def add(task_type, title, detail, priority, value=None, unpinned_only=False):
        if await _has_open_task(db, field.id, task_type, unpinned_only):
            return
        t = FieldTask(
            field_id=field.id, task_type=task_type, title=title, detail=detail,
            priority=priority, recommended_value=value, due_date=date.today(),
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
            await add("riego", f"Revisar estrés hídrico en {field.name}",
                      f"NDMI muy bajo ({means['NDMI']:.2f}). Si la vid NO está en maduración, considerar "
                      f"riego; si está post-envero, un déficit moderado (RDI) es deseable para la calidad.", 2)
    elif ndmi_eval and ndmi_eval["status"] == "critical":
        mm = _irrigation_mm(means["NDMI"], field.crop_type)
        await add("riego", f"Regar {field.name}", f"Humedad foliar crítica (NDMI {means['NDMI']:.2f}).",
                  1, f"{mm} mm")
    elif ndmi_eval and ndmi_eval["status"] == "warning":
        mm = _irrigation_mm(means["NDMI"], field.crop_type)
        await add("riego", f"Regar {field.name} (preventivo)",
                  f"Humedad foliar por debajo del umbral (NDMI {means['NDMI']:.2f}).", 2, f"{mm} mm")

    # ── Fertilización (clorofila NDRE) ────────────────────────────────────────
    # N dose comes from the prescription module so it matches the crop (e.g. vines
    # get ~40 kg N/ha, not a vegetable's 120) instead of a hardcoded value.
    ndre_eval = evals.get("NDRE")
    dose = base_dose_for(field.crop_type)
    if ndre_eval and ndre_eval["status"] == "critical":
        await add("fertilizacion", f"Fertilizar {field.name}",
                  f"Clorofila/nitrógeno bajo (NDRE {means['NDRE']:.2f}).", 1, f"{round(dose)} kg N/ha")
    elif ndre_eval and ndre_eval["status"] == "warning":
        await add("fertilizacion", f"Revisar nutrición de {field.name}",
                  f"NDRE bajo el umbral de aviso ({means['NDRE']:.2f}).", 3, f"{round(dose / 2)} kg N/ha")

    # ── Inspección (vegetación NDVI) ──────────────────────────────────────────
    ndvi = means.get("NDVI")
    if ndvi is not None and ndvi < 0.25:
        await add("inspeccion", f"Inspeccionar {field.name}",
                  f"Vegetación muy baja (NDVI {ndvi:.2f}). Posible estrés o daño.", 1,
                  unpinned_only=True)

    if created:
        await db.flush()
        logger.info(f"Generated {len(created)} task(s) for field {field.id}")
    return created


async def generate_tasks_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Generate tasks across all of the user's fields. Returns count created."""
    r = await db.execute(select(Field).where(Field.user_id == user_id))
    fields = r.scalars().all()
    total = 0
    for f in fields:
        total += len(await generate_tasks_for_field(db, f))
    await db.commit()
    return total
