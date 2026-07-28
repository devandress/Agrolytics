"""Billing endpoints — per-hectare plan catalog + MercadoPago Suscripciones.

Real money moves ONLY when ``MERCADOPAGO_ACCESS_TOKEN`` is set. Without it every
endpoint falls back to a labelled preview (no charge, no init_point) so local dev
and demos keep working. Use MercadoPago's TEST-... credentials
(https://www.mercadopago.com.mx/developers/panel/app) for sandbox testing before
ever putting a live APP_USR-... token in production.

Uses MercadoPago's recurring-subscription product (``/preapproval``, "Suscripciones"
in their dashboard) rather than Checkout Pro's one-time preferences — the price is
per hectare and billed monthly, so a subscription that renews itself is the right
fit; Checkout Pro would make the customer manually re-pay every month.

Flow: POST /checkout creates a preapproval and returns its ``init_point`` (a hosted
authorization page) — the frontend redirects the user there. The FIRST charge
happens at authorization; MercadoPago then bills automatically every month after.
MercadoPago calls POST /billing/webhook/mercadopago on status changes — that's the
ONLY place a paid plan gets granted, and it also downgrades the account back to
free if the subscription is cancelled or paused (e.g. a card stops working).
/subscribe only handles the free plan (no payment involved) — it never grants a
paid plan. POST /cancel lets the user stop the subscription themselves.
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
        "has_subscription": bool(current_user.mercadopago_preapproval_id),
    }


@router.post("/checkout")
async def checkout(body: CheckoutBody, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Start a recurring MercadoPago subscription (preapproval) for the Productor plan.

    Free needs no payment; Enterprise is a manual quote — neither goes through here.
    The first charge happens when the user authorizes on MercadoPago's page; every
    month after that, MercadoPago bills the same amount automatically.
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
            "message": "Modo sandbox: sin MERCADOPAGO_ACCESS_TOKEN configurado, no se puede iniciar una suscripción real todavía.",
            "plan": body.plan,
            "amount_mxn": amount,
            "hectares": price.get("total_ha"),
        }

    payload = {
        "reason": f"Agrolytics Productor — {price.get('total_ha', 0)} ha",
        "external_reference": f"{current_user.id}:{body.plan}",
        "payer_email": current_user.email,
        "auto_recurring": {
            "frequency": 1,
            "frequency_type": "months",
            "transaction_amount": float(amount),
            "currency_id": "MXN",
        },
        "back_url": f"{settings.PUBLIC_BASE_URL}/",
    }
    headers = {"Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{_MP_API}/preapproval", json=payload, headers=headers)
        resp.raise_for_status()
        pref = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"MercadoPago rechazó la solicitud: {exc.response.text[:300]}") from exc
    except Exception as exc:
        raise HTTPException(502, f"No se pudo conectar con MercadoPago: {exc}") from exc

    current_user.mercadopago_preapproval_id = pref.get("id")
    await db.commit()

    sandbox = settings.MERCADOPAGO_ACCESS_TOKEN.startswith("TEST-")
    return {
        "preview": False,
        "sandbox": sandbox,
        "init_point": pref.get("init_point"),
        "preapproval_id": pref.get("id"),
        "amount_mxn": amount,
        "hectares": price.get("total_ha"),
    }


@router.post("/cancel")
async def cancel_subscription(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Cancel the user's own recurring subscription and downgrade to Explorador."""
    if not current_user.mercadopago_preapproval_id:
        raise HTTPException(400, "No tenés una suscripción activa para cancelar.")
    if settings.MERCADOPAGO_ACCESS_TOKEN:
        headers = {"Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.put(
                    f"{_MP_API}/preapproval/{current_user.mercadopago_preapproval_id}",
                    json={"status": "cancelled"}, headers=headers,
                )
            resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(502, f"No se pudo cancelar en MercadoPago: {exc}") from exc
    current_user.plan = "free"
    current_user.mercadopago_preapproval_id = None
    await db.commit()
    return {"plan": "free", "message": "Suscripción cancelada. Cambiaste al plan Explorador."}


@router.post("/webhook/mercadopago", include_in_schema=False)
async def mercadopago_webhook(request: Request, db: DBSession) -> dict[str, Any]:
    """MercadoPago calls this on subscription status changes. No auth — verify via
    the preapproval lookup itself (MercadoPago's recommended pattern).

    ``type=subscription_preapproval`` (or legacy ``topic=preapproval``) fires when a
    subscription is created/authorized/cancelled/paused — that's what grants OR
    revokes the paid plan. ``subscription_authorized_payment`` fires for each
    recurring monthly charge; acknowledged but not acted on for now (the plan is
    already active from the preapproval, and a failed recurring charge eventually
    cancels/pauses the preapproval itself, which this same handler reacts to).
    """
    if not settings.MERCADOPAGO_ACCESS_TOKEN:
        return {"ok": False, "reason": "not configured"}

    params = request.query_params
    kind = params.get("type") or params.get("topic")  # legacy IPN uses "topic"
    entity_id = params.get("data.id") or params.get("id")
    if kind not in ("subscription_preapproval", "preapproval") or not entity_id:
        return {"ok": True, "ignored": kind or "no-type"}

    headers = {"Authorization": f"Bearer {settings.MERCADOPAGO_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{_MP_API}/preapproval/{entity_id}", headers=headers)
        resp.raise_for_status()
        preapproval = resp.json()
    except Exception:
        return {"ok": False, "reason": "lookup failed"}

    ref = preapproval.get("external_reference") or ""
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

    mp_status = preapproval.get("status")
    if mp_status == "authorized":
        user.plan = plan
        user.mercadopago_preapproval_id = entity_id
    elif mp_status in ("cancelled", "paused"):
        # Card declined, user cancelled from MercadoPago's own UI, etc. — the
        # subscription stopped paying, so the paid plan stops being granted.
        user.plan = "free"
        user.mercadopago_preapproval_id = None
    await db.commit()
    return {"ok": True, "status": mp_status, "plan": user.plan}


@router.post("/subscribe")
async def subscribe(body: SubscribeBody, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Switch to the free plan (no payment). Paid plans only activate via the
    MercadoPago webhook after a real subscription authorization — this endpoint
    can't grant them.
    """
    if body.plan != "free":
        raise HTTPException(
            status_code=400,
            detail="Para Productor usa /billing/checkout (suscripción real). Cooperativa se activa por contacto.",
        )
    current_user.plan = "free"
    await db.commit()
    return {"plan": "free", "message": "Cambiaste al plan Explorador."}
