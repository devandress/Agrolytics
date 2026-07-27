"""Billing endpoints — plan catalog, current plan + usage, and a SIMULATED checkout.

⚠️ No real money moves here. ``/checkout`` returns a preview object for the chosen
gateway (Stripe / PayPal / MercadoPago) and ``/subscribe`` just sets the user's plan.
Wire a real Merchant of Record (Lemon Squeezy/Paddle) or gateway before charging.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.models.field import Field
from app.services.plans import ORDER, PLANS, PRICING_NOTE, get_plan

router = APIRouter()

# Payment gateways shown in the preview (simulación).
GATEWAYS = [
    {"key": "stripe", "name": "Stripe", "desc": "Tarjeta internacional"},
    {"key": "paypal", "name": "PayPal", "desc": "Cuenta PayPal o tarjeta"},
    {"key": "mercadopago", "name": "MercadoPago", "desc": "Pagos locales LATAM"},
]


class CheckoutBody(BaseModel):
    plan: str
    gateway: str


class SubscribeBody(BaseModel):
    plan: str


@router.get("/plans")
async def list_plans(current_user: CurrentUser) -> dict[str, Any]:
    """Catalog of plans + gateways + the pricing disclaimer."""
    return {
        "plans": [PLANS[k] for k in ORDER],
        "gateways": GATEWAYS,
        "current_plan": current_user.plan,
        "note": PRICING_NOTE,
    }


@router.get("/me")
async def my_billing(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Current plan + usage (fields used vs. plan limit)."""
    used = (await db.execute(
        select(func.count(Field.id)).where(Field.user_id == current_user.id))).scalar_one()
    plan = get_plan(current_user.plan)
    return {
        "plan": plan,
        "usage": {
            "fields_used": used,
            "fields_limit": plan["max_fields"],
        },
        "note": PRICING_NOTE,
    }


@router.post("/checkout")
async def checkout(body: CheckoutBody, current_user: CurrentUser) -> dict[str, Any]:
    """Return a SIMULATED checkout for the chosen plan + gateway (no charge)."""
    if body.plan not in PLANS:
        raise HTTPException(400, "Plan inválido.")
    if body.gateway not in {g["key"] for g in GATEWAYS}:
        raise HTTPException(400, "Pasarela inválida.")
    plan = PLANS[body.plan]
    return {
        "simulated": True,
        "message": "PREVIEW — no se realizará ningún cobro.",
        "gateway": body.gateway,
        "plan": plan["key"],
        "amount_usd": plan["price_usd_month"],
        "checkout_id": f"sim_{body.gateway}_{uuid.uuid4().hex[:10]}",
    }


@router.post("/subscribe")
async def subscribe(body: SubscribeBody, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Simulate a successful subscription by setting the user's plan.

    SECURITY: this sets the plan WITHOUT real payment. Disabled in production so a
    user cannot self-upgrade for free — wire a real gateway/MoR webhook first.
    """
    if settings.is_production:
        raise HTTPException(
            status_code=501,
            detail="El cobro real aún no está configurado. Conecta una pasarela/MoR antes de producción.",
        )
    if body.plan not in PLANS:
        raise HTTPException(400, "Plan inválido.")
    current_user.plan = body.plan
    await db.commit()
    return {"plan": body.plan, "message": f"Plan actualizado a {PLANS[body.plan]['name']} (simulado)."}
