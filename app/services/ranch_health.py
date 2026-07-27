"""Ranch-level health aggregation for the Dueño (owner) view.

Rolls every field's latest vegetation/moisture signals into a single 0–100 score
(area-weighted), produces a per-field breakdown, and attaches the demo financial /
SGMA block from ``app.core.demo_data``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_data import financial_summary
from app.models.field import Field
from app.models.index import Index
from app.services.anomaly import evaluate_field_thresholds


async def _latest(db: AsyncSession, field_id: uuid.UUID, idx: str) -> float | None:
    r = await db.execute(
        select(Index.mean_value)
        .where(Index.field_id == field_id, Index.index_type == idx, Index.mean_value.isnot(None))
        .order_by(Index.date.desc())
        .limit(1)
    )
    v = r.scalar_one_or_none()
    return float(v) if v is not None else None


def _field_score(ndvi: float | None, ndmi: float | None, crop: str | None) -> tuple[int, str]:
    """Return a 0–100 health score and label for one field.

    Blends NDVI (vegetation vigour) with the NDMI threshold status for the crop.
    """
    if ndvi is None and ndmi is None:
        return -1, "sin datos"
    # NDVI component: map 0.2..0.8 → 0..100.
    veg = 0 if ndvi is None else max(0.0, min(1.0, (ndvi - 0.2) / 0.6))
    # Moisture component from threshold status.
    moist = 0.7
    if ndmi is not None:
        ev = evaluate_field_thresholds(crop, {"NDMI": ndmi}).get("NDMI")
        moist = {"ok": 1.0, "warning": 0.5, "critical": 0.15}.get(ev["status"], 0.7) if ev else 0.7
    score = round((0.6 * veg + 0.4 * moist) * 100)
    label = "sano" if score >= 70 else ("atención" if score >= 45 else "crítico")
    return score, label


async def ranch_overview(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Aggregate all of a user's fields into an owner-facing overview."""
    r = await db.execute(select(Field).where(Field.user_id == user_id))
    fields = r.scalars().all()

    breakdown: list[dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0.0
    counts = {"sano": 0, "atención": 0, "crítico": 0, "sin datos": 0}

    for f in fields:
        ndvi = await _latest(db, f.id, "NDVI")
        ndmi = await _latest(db, f.id, "NDMI")
        score, label = _field_score(ndvi, ndmi, f.crop_type)
        counts[label] = counts.get(label, 0) + 1
        breakdown.append({
            "field_id": str(f.id), "name": f.name, "crop_type": f.crop_type,
            "area_ha": f.area_ha, "score": score if score >= 0 else None, "status": label,
            "ndvi": round(ndvi, 3) if ndvi is not None else None,
            "ndmi": round(ndmi, 3) if ndmi is not None else None,
        })
        if score >= 0:
            w = f.area_ha or 1.0
            weighted_sum += score * w
            weight_total += w

    ranch_score = round(weighted_sum / weight_total) if weight_total else None
    ranch_label = (
        "sano" if (ranch_score or 0) >= 70 else "atención" if (ranch_score or 0) >= 45 else "crítico"
    ) if ranch_score is not None else "sin datos"

    # Resumen ejecutivo en español
    if ranch_score is None:
        summary = "Aún no hay datos satelitales suficientes para calcular la salud del rancho."
    else:
        problem = counts["crítico"] + counts["atención"]
        summary = (
            f"Salud general del rancho: {ranch_score}/100 ({ranch_label}). "
            + (f"{problem} de {len(fields)} parcelas requieren atención."
               if problem else "Todas las parcelas están en buen estado.")
        )

    return {
        "ranch_score": ranch_score,
        "ranch_status": ranch_label,
        "total_fields": len(fields),
        "field_status_counts": counts,
        "fields": sorted(breakdown, key=lambda x: (x["score"] is None, x["score"] or 0)),
        "executive_summary": summary,
        "financial": financial_summary(),  # demo block, labelled is_demo
    }
