"""Dashboard summary endpoint."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field
from app.models.insight import Insight
from app.schemas.dashboard import DashboardResponse

router = APIRouter()


@router.get("/{user_id}", response_model=DashboardResponse)
async def get_dashboard(
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> DashboardResponse:
    """Return an aggregated overview for *user_id*.

    A regular user can only view their own dashboard; admins can view any.
    """
    if user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    # ── Field stats ────────────────────────────────────────────────────────────
    fields_result = await db.execute(
        select(func.count(Field.id), func.coalesce(func.sum(Field.area_ha), 0.0)).where(
            Field.user_id == user_id
        )
    )
    field_count, total_area = fields_result.one()

    # ── Active stress alerts (purchased or not — always shown in dashboard) ───
    alerts_result = await db.execute(
        select(Insight)
        .join(Field, Insight.field_id == Field.id)
        .where(Field.user_id == user_id, Insight.insight_type == "stress_alert")
        .order_by(Insight.date.desc())
        .limit(10)
    )
    alerts: list[dict[str, Any]] = [
        {
            "id": str(a.id),
            "field_id": str(a.field_id),
            "date": str(a.date),
            "preview": a.content.get("summary") if a.content else None,
            "is_purchased": a.is_purchased,
        }
        for a in alerts_result.scalars().all()
    ]

    # ── Latest prescriptions ──────────────────────────────────────────────────
    rx_result = await db.execute(
        select(Insight)
        .join(Field, Insight.field_id == Field.id)
        .where(
            Field.user_id == user_id,
            Insight.insight_type == "prescription",
            Insight.is_purchased == True,  # noqa: E712
        )
        .order_by(Insight.date.desc())
        .limit(5)
    )
    prescriptions: list[dict[str, Any]] = [
        {
            "id": str(p.id),
            "field_id": str(p.field_id),
            "date": str(p.date),
            "summary": p.content.get("summary") if p.content else None,
        }
        for p in rx_result.scalars().all()
    ]

    # ── Yield estimates ───────────────────────────────────────────────────────
    yield_result = await db.execute(
        select(Insight)
        .join(Field, Insight.field_id == Field.id)
        .where(
            Field.user_id == user_id,
            Insight.insight_type == "yield_est",
            Insight.is_purchased == True,  # noqa: E712
        )
        .order_by(Insight.date.desc())
        .limit(5)
    )
    yield_estimates: list[dict[str, Any]] = [
        {
            "id": str(y.id),
            "field_id": str(y.field_id),
            "date": str(y.date),
            "yield_t_ha": y.content.get("yield_t_ha") if y.content else None,
        }
        for y in yield_result.scalars().all()
    ]

    return DashboardResponse(
        user_id=user_id,
        total_fields=field_count,
        total_area_ha=float(total_area),
        active_stress_alerts=alerts,
        latest_prescriptions=prescriptions,
        yield_estimates=yield_estimates,
    )
