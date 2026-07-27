"""Crop water requirement: stage-aware Kc, effective rainfall, net irrigation.

Unifies the platform's irrigation math. Instead of a single per-crop ET0
multiplier, this applies a growth-stage-dependent crop coefficient (Kc, FAO-56
style) and an effective-rainfall estimate, turning real weather into a defensible
**daily net irrigation requirement** (mm):

    ETc      = ET0 × Kc(stage)
    net (mm) = max(0, target_ETc − effective_rainfall)

Vineyards additionally support **regulated deficit irrigation (RDI)**: post-veraison
a controlled water deficit is *desirable* for wine quality, so the target is reduced
on purpose rather than flagged as an urgent deficit. Values are FAO-56-inspired and
rounded for transparency — calibrate per region with grower records.
"""

from __future__ import annotations

# Stage-aware crop coefficients (Kc). Stage keys map to phenology labels via
# _STAGE_TO_KC below. Vegetables peak near canopy closure; vines run much lower.
_KC: dict[str, dict[str, float]] = {
    "Lechuga":   {"ini": 0.70, "dev": 0.85, "mid": 1.00, "end": 0.95},
    "Espinaca":  {"ini": 0.70, "dev": 0.85, "mid": 1.00, "end": 0.95},
    "Brócoli":   {"ini": 0.70, "dev": 0.85, "mid": 1.05, "end": 0.95},
    "Coliflor":  {"ini": 0.70, "dev": 0.85, "mid": 1.05, "end": 0.95},
    "Fresa":     {"ini": 0.40, "dev": 0.70, "mid": 0.85, "end": 0.75},
    "Apio":      {"ini": 0.70, "dev": 0.90, "mid": 1.05, "end": 1.00},
    "Alcachofa": {"ini": 0.50, "dev": 0.70, "mid": 1.00, "end": 0.95},
    # Wine grapes (FAO-56 table 12, drip): low and capped; RDI reduces it further.
    "Vid":       {"ini": 0.30, "dev": 0.50, "mid": 0.70, "end": 0.45},
}
_DEFAULT_KC = {"ini": 0.50, "dev": 0.75, "mid": 1.00, "end": 0.85}

# Phenology stage label (from app.services.phenology.STAGES) → Kc stage key.
_STAGE_TO_KC: dict[str, str] = {
    "establecimiento": "ini",
    "crecimiento vegetativo": "dev",
    "llenado / madurez": "mid",
    "lista para cosecha": "end",
}

_VINE_NAMES = {"vid", "uva", "grape", "vineyard"}
# Fraction of full ETc targeted during vine ripening (regulated deficit irrigation).
_RDI_DEFICIT = 0.60
# Ripening stages where RDI applies (post-veraison): canopy is built, deficit is the goal.
_RDI_STAGES = {"llenado / madurez", "lista para cosecha"}


def is_vine(crop_type: str | None) -> bool:
    return (crop_type or "").strip().lower() in _VINE_NAMES


def kc_for(crop_type: str | None, stage_label: str | None) -> float:
    """Crop coefficient for a crop at a phenology stage (falls back to mid/default)."""
    table = _KC.get((crop_type or "").strip(), _DEFAULT_KC)
    key = _STAGE_TO_KC.get((stage_label or "").strip().lower(), "mid")
    return table[key]


def effective_rainfall(precip_mm: float | None) -> float:
    """Portion of rainfall actually available to the crop (mm).

    Simplified daily estimate: events under ~2.5 mm are lost to interception and
    evaporation; larger events are ~80% effective (the rest to runoff/deep drainage).
    """
    p = max(0.0, precip_mm or 0.0)
    if p < 2.5:
        return 0.0
    return round(min(0.8 * p, p - 2.0), 1)


def rdi_active(crop_type: str | None, stage_label: str | None) -> bool:
    """True when regulated deficit irrigation is the target (vines, post-veraison)."""
    return is_vine(crop_type) and (stage_label or "").strip().lower() in _RDI_STAGES


def daily_water_need(
    et0_mm: float | None,
    crop_type: str | None,
    stage_label: str | None,
    precip_mm: float | None,
) -> dict:
    """Daily net irrigation requirement from real ET0 + rainfall + stage-aware Kc.

    Returns the full breakdown so the agronomist sees *why*: Kc, ETc, the RDI target,
    effective rainfall and the resulting net irrigation (mm).
    """
    et0 = max(0.0, et0_mm or 0.0)
    kc = kc_for(crop_type, stage_label)
    etc = et0 * kc

    rdi = rdi_active(crop_type, stage_label)
    target = etc * _RDI_DEFICIT if rdi else etc

    eff_rain = effective_rainfall(precip_mm)
    net = max(0.0, round(target - eff_rain, 1))
    return {
        "kc": round(kc, 2),
        "etc_mm": round(etc, 1),
        "target_etc_mm": round(target, 1),
        "effective_rain_mm": eff_rain,
        "net_irrigation_mm": net,
        "rdi": rdi,
    }
