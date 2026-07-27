"""Insight generation, retrieval, and purchase endpoints."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field
from app.models.insight import Insight
from app.schemas.insight import InsightBuyResponse, InsightGenerateResponse, InsightOut
from app.tasks.insight_tasks import generate_insights_task

router = APIRouter()

# A separate sub-router that will be mounted under /fields in the main router
fields_router = APIRouter()

# Price map by insight type (USD)
INSIGHT_PRICES: dict[str, float] = {
    "zone_map": 4.99,
    "stress_alert": 2.99,
    "prescription": 9.99,
    "yield_est": 7.99,
}


@fields_router.post("/{field_id}/insights/generate", response_model=InsightGenerateResponse)
async def generate_insights(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> InsightGenerateResponse:
    """Dispatch an async Celery task to compute all insights for a field.

    Returns a **task_id** that can be polled via Celery's result backend.
    """
    owned = await db.execute(
        select(Field.id).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    if not owned.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Field not found.")

    task = generate_insights_task.delay(str(field_id))
    return InsightGenerateResponse(
        task_id=task.id,
        message=f"Insight generation started. Poll task {task.id} for status.",
    )


@router.get("/{insight_id}", response_model=InsightOut)
async def get_insight(
    insight_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> InsightOut:
    """Retrieve an insight.

    If ``is_purchased`` is False, ``content`` is replaced with a preview stub
    unless the caller is an admin.
    """
    result = await db.execute(
        select(Insight, Field.user_id)
        .join(Field, Insight.field_id == Field.id)
        .where(Insight.id == insight_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found.")

    insight: Insight = row[0]
    owner_id: uuid.UUID = row[1]

    if owner_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied.")

    out = InsightOut.model_validate(insight)

    # Mask full content for unpurchased insights (non-admin)
    if not insight.is_purchased and current_user.role != "admin":
        out.content = {
            "preview": True,
            "insight_type": insight.insight_type,
            "message": "Purchase this insight to unlock full content.",
            "price_usd": insight.price_usd,
        }

    return out


@router.post("/{insight_id}/buy", response_model=InsightBuyResponse)
async def buy_insight(
    insight_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> InsightBuyResponse:
    """Simulate payment and mark the insight as purchased."""
    result = await db.execute(
        select(Insight, Field.user_id)
        .join(Field, Insight.field_id == Field.id)
        .where(Insight.id == insight_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found.")

    insight: Insight = row[0]
    owner_id: uuid.UUID = row[1]

    if owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied.")

    if insight.is_purchased:
        return InsightBuyResponse(
            id=insight.id,
            is_purchased=True,
            message="Insight already purchased.",
        )

    # ── Simulated payment ─────────────────────────────────────────────────────
    # In production: call PayPal / Stripe API here, verify transaction.
    insight.is_purchased = True
    await db.commit()

    return InsightBuyResponse(
        id=insight.id,
        is_purchased=True,
        message="Payment successful. Insight unlocked.",
    )
