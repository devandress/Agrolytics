"""Orchestrates all insight types for a given field.

Called from the Celery task; runs inside a synchronous DB session.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.models.field import Field
from app.models.insight import Insight
from app.services.clustering import compute_zone_map
from app.services.prescription import generate_prescription
from app.services.stress_alert import detect_stress
from app.services.yield_estimation import estimate_yield

_PRICES: dict[str, float] = {
    "zone_map": 4.99,
    "stress_alert": 2.99,
    "prescription": 9.99,
    "yield_est": 7.99,
}


def generate_all_insights(field_id: str, db: Session) -> list[uuid.UUID]:
    """Compute and persist all insight types for *field_id*.

    Returns a list of newly created Insight UUIDs.
    """
    field: Field | None = db.query(Field).filter_by(id=uuid.UUID(field_id)).first()
    if not field:
        raise ValueError(f"Field {field_id} not found.")

    today = date.today()
    created_ids: list[uuid.UUID] = []

    # ── 1. Management zone map ────────────────────────────────────────────────
    zone_content: dict[str, Any] | None = None
    try:
        zone_content = compute_zone_map(field, db)
        _upsert_insight(db, field.id, today, "zone_map", zone_content, _PRICES["zone_map"])
        logger.info(f"Zone map created for field {field_id}")
    except Exception as exc:
        logger.warning(f"Zone map failed for field {field_id}: {exc}")

    # ── 2. Stress alert ───────────────────────────────────────────────────────
    try:
        stress = detect_stress(field, db)
        insight = _upsert_insight(db, field.id, today, "stress_alert", stress, _PRICES["stress_alert"])
        created_ids.append(insight.id)
        logger.info(f"Stress alert created for field {field_id}: alert={stress['alert']}")
    except Exception as exc:
        logger.warning(f"Stress alert failed for field {field_id}: {exc}")

    # ── 3. Prescription ───────────────────────────────────────────────────────
    if zone_content:
        try:
            rx = generate_prescription(zone_content, field.crop_type)
            insight = _upsert_insight(db, field.id, today, "prescription", rx, _PRICES["prescription"])
            created_ids.append(insight.id)
        except Exception as exc:
            logger.warning(f"Prescription failed for field {field_id}: {exc}")

    # ── 4. Yield estimate ─────────────────────────────────────────────────────
    try:
        ye = estimate_yield(field, db)
        insight = _upsert_insight(db, field.id, today, "yield_est", ye, _PRICES["yield_est"])
        created_ids.append(insight.id)
    except Exception as exc:
        logger.warning(f"Yield estimate failed for field {field_id}: {exc}")

    db.commit()
    return created_ids


def _upsert_insight(
    db: Session,
    field_id: uuid.UUID,
    today: date,
    insight_type: str,
    content: dict[str, Any],
    price: float,
) -> Insight:
    """Create or update today's insight record for the given type."""
    existing: Insight | None = (
        db.query(Insight)
        .filter_by(field_id=field_id, date=today, insight_type=insight_type)
        .first()
    )
    if existing:
        existing.content = content
        db.flush()
        return existing

    insight = Insight(
        field_id=field_id,
        date=today,
        insight_type=insight_type,
        content=content,
        is_purchased=False,
        price_usd=price,
    )
    db.add(insight)
    db.flush()
    return insight
