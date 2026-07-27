"""Billing endpoints — per-hectare plan catalog + MercadoPago Checkout Pro.

Real money moves ONLY when ``MERCADOPAGO_ACCESS_TOKEN`` is set. Without it every
endpoint falls back to a labelled preview (no charge, no init_point) so local dev
and demos keep working. Use MercadoPago's TEST-... credentials
(https://www.mercadopago.com.mx/developers/panel/app) for sandbox testing before
ever putting a live APP_USR-... token in production.

Flow: POST /checkout creates a MercadoPago Preference and returns its
``init_point`` (a hosted checkout URL) — the frontend redirects the user there.
MercadoPago calls POST /billing/webhook/mercadopago when the payment settles;
that's the ONLY place a paid plan gets activated. /subscribe only handles the
free plan (no payment involved) — it never grants a paid plan.
"""

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.models.field import Field
from app.models.user import User
from app.services.plans import ORDER, PLANS, PRICING_NOTE, get_plan, price_mxn_for_ha

router = APIRouter()

_MP_API = "https://api.mercadopago.com"

# Only one gateway, and only listed once it can actually take a payment. Showing a
# selectable button that does nothing is the same false-certainty mistake the pest
# model made — don't repeat it here with money.
GATEWAYS = [{"key": "mercadopago", "name": "MercadoPago", "desc": "Tarjeta, OXXO, transferencia — México"}]


class CheckoutBody(BaseModel):
    plan: str
    gateway: str = "mercadopago"


class SubscribeBody(BaseModel):
    plan: str


async def _total_ha(db: DBSession, user_id: uuid.UUID) -> float:
    total = (await db.execute(
        select(func.coalesce(func.sum(Field.area_ha), 0.0)).where(Field.user_id == user_id))).scalar_one()
    return float(total or 0.0)


@router.get("/plans")
async def list_plans(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Catalog of plans + gateway + the pricing disclaimer, priced for this user's own hectares."""
    ha = await _total_ha(db, current_user.id)
    return {
        "plans": [PLANS[k] for k in ORDER],
        "gateways": GATEWAYS,
        "current_plan": current_user.plan,
        "note": PRICING_NOTE,
        "your_hectares": round(ha, 2),
        "your_price_pro": price_mxn_for_ha("pro", ha),
        "sandbox": not bool(settings.MERCADOPAGO_ACCESS_TOKEN),
    }


@router.get("/me")
async def my_billing(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Current plan + usage (fields/hectares vs. plan limit) + live price for this account."""
    used = (await db.execute(
        select(func.count(Field.id)).where(Field.user_id == current_user.id))).scalar_one()
    ha = await _total_ha(db, current_user.id)
    plan = get_plan(current_user.plan)
    return {
        "plan": plan,
        "usage": {
            "fields_used": used,
            "fields_limit": plan["max_fields"],
            "hectares_used": round(ha, 2),
            "hectares_limit": plan["max_ha"],
        },
        "price": price_mxn_for_ha(current_user.plan, ha),
        "note": PRICING_NOTE,
    }


@router.post("/checkout")
async def checkout(body: CheckoutBody, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Create a MercadoPago Checkout Pro preference for the Productor plan.

    Free needs no payment; Enterprise is a manual quote — neither goes through here.
    """
    if body.plan != "pro":
        raise HTTPException(400, "Solo el plan Productor tiene cobro en línea. Explorador es gratis; "
                                  "Cooperativa se contrata por contacto directo.")
    ha = await _total_ha(db, current_user.id)
    price = price_mxn_for_ha("pro", ha)
    amount = price["mxn_month"]

    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        return {
            "preview": True,
            "message": "Modo sandbox: sin MERCADOPAGO_ACCESS_TOKEN configurado, no se puede iniciar un cobro real todavía.",
            "plan": body.plan,
            "amount_mxn": amount,
            "hectares": price.get("total_ha"),
        }

    payload = {
        "items": [{
            "title": f"Agrolytics Productor — {price.get('total_ha', 0)} ha",
            "quantity": 1,
            "currency_id": "MXN",
            "unit_price": float(amount),
        }],
        "payer": {"email": current_user.email},
        "back_urls": {
            "success": f"{settings.PUBLIC_BASE_URL}/#/billing/success",
            "failure": f"{settings.PUBLIC_BASE_URL}/#/billing/failure",
            "pending": f"{settings.PUBLIC_BASE_URL}/#/billing/pending",
        },
        "auto_return": "approved",
        "notification_url": f"{settings.PUBLIC_BASE_URL}/api/v1/billing/webhook/mercadopago",
        "external_reference": f"{current_user.id}:{body.plan}",
    }
    headers = {"Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{_MP_API}/checkout/preferences", json=payload, headers=headers)
        resp.raise_for_status()
        pref = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"MercadoPago rechazó la solicitud: {exc.response.text[:300]}") from exc
    except Exception as exc:
        raise HTTPException(502, f"No se pudo conectar con MercadoPago: {exc}") from exc

    sandbox = settings.MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")
    return {
        "preview": False,
        "sandbox": sandbox,
        "init_point": pref.get("sandbox_init_point") if sandbox else pref.get("init_point"),
        "preference_id": pref.get("id"),
        "amount_mxn": amount,
        "hectares": price.get("total_ha"),
    }


@router.post("/webhook/mercadopago", include_in_schema=False)
async def mercadopago_webhook(request: Request, db: DBSession) -> dict[str, Any]:
    """MercadoPago calls this after a payment settles. No auth — verify via the
    payment lookup itself (MercadoPago's recommended pattern for Checkout Pro).
    """
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        return {"ok": False, "reason": "not configured"}

    params = request.query_params
    kind = params.get("type") or params.get("topic")  # legacy IPN uses "topic"
    payment_id = params.get("data.id") or params.get("id")
    if kind != "payment" or not payment_id:
        return {"ok": True, "ignored": kind or "no-type"}

    headers = {"Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{_MP_API}/v1/payments/{payment_id}", headers=headers)
        resp.raise_for_status()
        payment = resp.json()
    except Exception:
        return {"ok": False, "reason": "lookup failed"}

    if payment.get("status") != "approved":
        return {"ok": True, "status": payment.get("status")}

    ref = payment.get("external_reference") or ""
    if ":" not in ref:
        return {"ok": False, "reason": "bad external_reference"}
    user_id_str, plan = ref.split(":", 1)
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return {"ok": False, "reason": "bad user id"}
    if plan not in PLANS:
        return {"ok": False, "reason": "bad plan"}

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return {"ok": False, "reason": "user not found"}
    user.plan = plan
    await db.commit()
    return {"ok": True, "plan": plan}


@router.post("/subscribe")
async def subscribe(body: SubscribeBody, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Switch to the free plan (no payment). Paid plans only activate via the
    MercadoPago webhook after a real charge — this endpoint can't grant them.
    """
    if body.plan != "free":
        raise HTTPException(
            status_code=400,
            detail="Para Productor usa /billing/checkout (pago real). Cooperativa se activa por contacto.",
        )
    current_user.plan = "free"
    await db.commit()
    return {"plan": "free", "message": "Cambiaste al plan Explorador."}
