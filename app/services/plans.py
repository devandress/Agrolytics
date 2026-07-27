"""Subscription plans — limits, features and PROVISIONAL pricing.

Pricing is a placeholder until the user runs a market test and measures unit costs.
The real cost drivers are AI calls (DeepSeek), raster compute, storage and hosting
(satellite data itself is free), so plans gate **fields** and **AI usage**.

Edit the numbers here; everything else (UI, limits) reads from this single source.
"""

from __future__ import annotations

# max_fields: None = unlimited. ai: monthly AI proposal/chat allowance (None = unlimited).
PLANS: dict[str, dict] = {
    "free": {
        "key": "free",
        "name": "Explorador",
        "price_usd_month": 0,
        "price_label": "Gratis",
        "max_fields": 1,
        "ai_monthly": 0,            # sin asistente IA
        "indices": ["NDVI", "NDMI"],
        "radar_fusion": False,
        "export": False,
        "tagline": "Para probar AgroVision en una parcela.",
        "features": [
            "1 parcela",
            "Índices NDVI y NDMI",
            "Clima y alertas básicas",
        ],
    },
    "pro": {
        "key": "pro",
        "name": "Productor",
        "price_usd_month": 29,       # PROVISIONAL — ajustar tras prueba de mercado
        "price_label": "$29/mes",
        "max_fields": 10,
        "ai_monthly": 300,
        "indices": ["NDVI", "NDMI", "NDRE", "EVI", "RVI"],
        "radar_fusion": True,
        "export": True,
        "tagline": "Para productores que gestionan varias parcelas.",
        "features": [
            "Hasta 10 parcelas",
            "Todos los índices + radar + fusión multi-satélite",
            "Asistente IA (300 consultas/mes)",
            "Alertas y exportación de datos",
        ],
    },
    "enterprise": {
        "key": "enterprise",
        "name": "Cooperativa",
        "price_usd_month": None,     # custom / contacto
        "price_label": "Contactar",
        "max_fields": None,
        "ai_monthly": None,
        "indices": ["NDVI", "NDMI", "NDRE", "EVI", "RVI", "VHVV"],
        "radar_fusion": True,
        "export": True,
        "tagline": "Cooperativas y agronegocios a escala.",
        "features": [
            "Parcelas ilimitadas",
            "Multiusuario y portafolio",
            "API y white-label",
            "Soporte dedicado",
        ],
    },
}

# Pricing is provisional until validated with a market test.
PRICING_NOTE = "Precios provisionales — sujetos a prueba de mercado."
ORDER = ["free", "pro", "enterprise"]


def get_plan(key: str | None) -> dict:
    return PLANS.get(key or "free", PLANS["free"])


def plan_allows_ai(key: str | None) -> bool:
    p = get_plan(key)
    return p["ai_monthly"] is None or p["ai_monthly"] > 0


def plan_max_fields(key: str | None) -> int | None:
    return get_plan(key)["max_fields"]
