"""Fixed DEMONSTRATION values for money and water/SGMA figures.

⚠️  These are NOT real measurements. Agrolytics has no cost-accounting or
regulatory-allocation data source yet, so the financial and SGMA water-budget
numbers shown to the Dueño/Regador roles are illustrative placeholders.

Every API response that uses these constants sets ``"is_demo": true`` and carries
a Spanish disclaimer so the UI can label them clearly. Centralised here so they
are trivial to find and replace with real integrations later.
"""

# ── Precios de referencia (demo) ───────────────────────────────────────────────
WATER_PRICE_USD_PER_M3 = 0.18        # USD por m³ de agua
FERTILIZER_PRICE_USD_PER_KG = 0.85   # USD por kg de nitrógeno (urea)

# ── Presupuesto de agua SGMA (demo) ─────────────────────────────────────────────
# Sustainable Groundwater Management Act — asignación ilustrativa por temporada.
SGMA_ALLOCATION_M3 = 480_000         # m³ asignados para la temporada
SGMA_USED_M3 = 311_000               # m³ usados hasta hoy

# ── Ahorro estimado del mes (demo) ──────────────────────────────────────────────
# Ahorro atribuido a actuar sobre las alertas (riego de precisión + dosis variable).
MONTHLY_SAVINGS_USD = 4_280
MONTHLY_WATER_SAVED_M3 = 18_500
MONTHLY_FERTILIZER_SAVED_KG = 1_240

# Texto que la UI muestra junto a cualquier cifra de esta fuente.
DEMO_DISCLAIMER = (
    "Valor de demostración — no proviene de datos reales de costos ni de "
    "asignación regulatoria. Reemplazar con integración contable / SGMA real."
)


def financial_summary() -> dict:
    """Return the demo money + SGMA block used by the Dueño / Regador views."""
    pct_used = round(SGMA_USED_M3 / SGMA_ALLOCATION_M3 * 100, 1)
    return {
        "is_demo": True,
        "disclaimer": DEMO_DISCLAIMER,
        "monthly_savings_usd": MONTHLY_SAVINGS_USD,
        "monthly_water_saved_m3": MONTHLY_WATER_SAVED_M3,
        "monthly_fertilizer_saved_kg": MONTHLY_FERTILIZER_SAVED_KG,
        "sgma": {
            "allocation_m3": SGMA_ALLOCATION_M3,
            "used_m3": SGMA_USED_M3,
            "remaining_m3": SGMA_ALLOCATION_M3 - SGMA_USED_M3,
            "pct_used": pct_used,
            "status": "ok" if pct_used < 80 else ("warning" if pct_used < 100 else "over"),
        },
    }
