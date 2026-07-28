"""Subscription plans — priced per hectare (MXN), not a flat seat price.

The real cost driver is near-zero and doesn't scale with hectares (AI calls,
compute) — see the pricing memo. What scales with hectares is the customer's
willingness to pay (more land → more water/fertilizer/yield at stake), so the
plan is metered by total hectares under management, with a floor so a 1 ha
account doesn't produce an invoice too small for card-processing fees to make
sense, and a volume discount so cooperatives aren't punished for scale.

Numbers are still a first hypothesis — validate with real conversations before
treating them as final (see PRICING_NOTE).
"""

from __future__ import annotations

MXN_PER_HA = 19          # Productor tier, first 20 ha
MXN_PER_HA_SCALE = 14    # Productor tier, hectare 21 and beyond
MXN_MINIMUM = 149        # floor per paying account, regardless of hectares
SCALE_THRESHOLD_HA = 20

# Explorador is capped by FIELD COUNT, not area. An area cap was tried (3 ha) and
# removed: a typical coastal-vegetable block in this market runs 10–40 ha, so any
# realistic first parcela got rejected at the last step of the signup wizard —
# the free tier was unusable by the exact customer it was meant to attract. The
# trial's value is "see it work on YOUR real field"; the limits that actually
# protect revenue are 1 parcela + the feature gates (NDVI/NDMI only, no AI, no
# export, no radar fusion), all enforced server-side.
FREE_MAX_HA = None

PLANS: dict[str, dict] = {
    "free": {
        "key": "free",
        "name": "Explorador",
        "price_label": "Gratis",
        "billing": "flat",
        "max_fields": 1,
        "max_ha": FREE_MAX_HA,
        "ai_monthly": 0,
        "indices": ["NDVI", "NDMI"],
        "radar_fusion": False,
        "export": False,
        "tagline": "Probá Agrolytics en una parcela real, del tamaño que sea.",
        "features": [
            "1 parcela, sin límite de hectáreas",
            "Índices NDVI y NDMI",
            "Clima y alertas básicas",
        ],
    },
    "pro": {
        "key": "pro",
        "name": "Productor",
        "price_label": f"${MXN_PER_HA} MXN/ha/mes",
        "billing": "per_ha",
        "max_fields": None,
        "max_ha": None,
        "ai_monthly": 300,
        "indices": ["NDVI", "NDMI", "NDRE", "EVI", "RVI"],
        "radar_fusion": True,
        "export": True,
        "tagline": f"Mínimo ${MXN_MINIMUM} MXN/mes (~{round(MXN_MINIMUM / MXN_PER_HA)} ha incluidas). Descuento automático desde la hectárea {SCALE_THRESHOLD_HA + 1}.",
        "features": [
            "Hectáreas ilimitadas, pagás por lo que gestionás",
            "Todos los índices + radar + fusión multi-satélite",
            "Plagas 360 (pronóstico 3 días + confirmación en campo)",
            "Asistente IA (300 consultas/mes)",
            "Alertas WhatsApp + exportación de datos",
        ],
    },
    "enterprise": {
        "key": "enterprise",
        "name": "Cooperativa",
        "price_label": "Contactar",
        "billing": "custom",
        "max_fields": None,
        "max_ha": None,
        "ai_monthly": None,
        "indices": ["NDVI", "NDMI", "NDRE", "EVI", "RVI", "VHVV"],
        "radar_fusion": True,
        "export": True,
        "tagline": "Desde $9–10 MXN/ha/mes a partir de 100 ha.",
        "features": [
            "Hectáreas ilimitadas con tarifa por volumen",
            "Multiusuario y portafolio",
            "API y white-label",
            "Soporte dedicado",
        ],
    },
}

PRICING_NOTE = "Precio por hectárea — hipótesis inicial, sujeta a validación con productores reales."
ORDER = ["free", "pro", "enterprise"]


def get_plan(key: str | None) -> dict:
    return PLANS.get(key or "free", PLANS["free"])


def plan_allows_ai(key: str | None) -> bool:
    p = get_plan(key)
    return p["ai_monthly"] is None or p["ai_monthly"] > 0


def plan_max_fields(key: str | None) -> int | None:
    return get_plan(key)["max_fields"]


def plan_max_ha(key: str | None) -> float | None:
    return get_plan(key).get("max_ha")


def plan_allows_index(key: str | None, index: str) -> bool:
    return (index or "").upper() in get_plan(key)["indices"]


def plan_allows_radar_fusion(key: str | None) -> bool:
    return bool(get_plan(key)["radar_fusion"])


def plan_allows_export(key: str | None) -> bool:
    return bool(get_plan(key)["export"])


def price_mxn_for_ha(plan_key: str, total_ha: float) -> dict:
    """Monthly MXN price for a plan given total hectares under management.

    Only "pro" is metered — free is flat ($0, limited by field count) and
    enterprise is a custom quote (no self-serve number to compute).
    """
    if plan_key == "free":
        over = FREE_MAX_HA is not None and total_ha > FREE_MAX_HA
        return {"mxn_month": 0, "billing": "flat", "over_ha_limit": over}
    if plan_key == "enterprise":
        return {"mxn_month": None, "billing": "custom"}

    base_ha = min(total_ha, SCALE_THRESHOLD_HA)
    scale_ha = max(0.0, total_ha - SCALE_THRESHOLD_HA)
    raw = base_ha * MXN_PER_HA + scale_ha * MXN_PER_HA_SCALE
    mxn_month = max(MXN_MINIMUM, round(raw))
    return {
        "mxn_month": mxn_month,
        "billing": "per_ha",
        "total_ha": round(total_ha, 2),
        "at_minimum": raw < MXN_MINIMUM,
        "effective_mxn_per_ha": round(mxn_month / total_ha, 2) if total_ha > 0 else None,
    }
