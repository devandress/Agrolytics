"""Biomass / canopy-vigour model from the satellite NDVI (and EVI) time-series.

Fresh biomass for leafy vegetables tracks accumulated greenness. We integrate the
NDVI series over the season (temporal integral of vigour) and map it to a biomass
estimate with per-crop coefficients, plus the phenological stage from the planting
date. All inputs are real satellite observations — no synthetic data.

Coefficients are first-order estimates; growers' harvest records calibrate them
(roadmap), at which point this becomes a fitted yield/biomass model.
"""

from __future__ import annotations

from datetime import date

# (a, b): biomass_t_ha ≈ a * NDVI_integral_per_day + b, per coastal crop.
_COEF: dict[str, tuple[float, float]] = {
    "Lechuga":   (55.0, -3.0),
    "Espinaca":  (40.0, -2.0),
    "Brócoli":   (45.0, -2.5),
    "Coliflor":  (45.0, -2.5),
    "Fresa":     (35.0, -1.5),
    "Apio":      (70.0, -4.0),
    "Alcachofa": (50.0, -3.0),
}
_DEFAULT = (45.0, -2.5)

# Approximate days to harvest per coastal crop (for phenology stage).
_CYCLE_DAYS: dict[str, int] = {
    "Lechuga": 75, "Espinaca": 45, "Brócoli": 90, "Coliflor": 95,
    "Fresa": 120, "Apio": 120, "Alcachofa": 150,
}


def _stage(days: int | None, crop: str | None) -> str:
    if days is None:
        return "desconocida"
    cycle = _CYCLE_DAYS.get((crop or "").strip(), 90)
    pct = days / cycle
    if pct < 0.25:
        return "establecimiento"
    if pct < 0.6:
        return "crecimiento vegetativo"
    if pct < 0.9:
        return "llenado / madurez"
    return "lista para cosecha"


def estimate_biomass(
    series: list[tuple[date, float]],
    crop_type: str | None,
    planting_date: date | None = None,
) -> dict:
    """Estimate biomass from an NDVI ``[(date, value)]`` series (oldest→newest).

    Returns biomass_t_ha, ndvi_integral, current NDVI, trend, growth stage and
    days since planting. ``status='insufficient_data'`` if there are no points.
    """
    pts = sorted([(d, float(v)) for d, v in series if v is not None], key=lambda x: x[0])
    if not pts:
        return {"status": "insufficient_data", "biomass_t_ha": None}

    # Temporal integral of NDVI (per-day average × season length proxy)
    if len(pts) >= 2:
        d0 = pts[0][0]
        xs = [(d - d0).days for d, _ in pts]
        ys = [v for _, v in pts]
        area = 0.0
        for i in range(1, len(xs)):
            area += (ys[i] + ys[i - 1]) / 2 * (xs[i] - xs[i - 1])
        span = max(1, xs[-1] - xs[0])
        ndvi_integral = area / span  # mean NDVI over the observed window
    else:
        ndvi_integral = pts[-1][1]

    a, b = _COEF.get((crop_type or "").strip(), _DEFAULT)
    biomass = max(0.0, round(a * ndvi_integral + b, 2))

    latest = pts[-1][1]
    first = pts[0][1]
    trend = "sube" if latest > first + 0.02 else "baja" if latest < first - 0.02 else "estable"

    days = (date.today() - planting_date).days if planting_date else None

    return {
        "status": "ok",
        "biomass_t_ha": biomass,
        "ndvi_integral": round(ndvi_integral, 3),
        "current_ndvi": round(latest, 3),
        "trend": trend,
        "n_obs": len(pts),
        "days_since_planting": days,
        "stage": _stage(days, crop_type),
        "is_model": True,
    }
