"""Demo pest & disease risk heuristic for the Agrónomo (PCA) portfolio view.

⚠️ Demonstration logic, not an agronomic model. Risk is inferred from the crop
type plus a moisture proxy (latest NDMI — high canopy moisture favours fungal
disease). A real implementation would use micro-climate, scouting history,
degree-days and validated phenology models.
"""

from __future__ import annotations

# Per-crop primary threat + the NDMI moisture level above which fungal pressure rises.
_CROP_THREATS: dict[str, dict] = {
    "Lechuga":   {"pest": "Mildiú velloso (downy mildew)", "moist_high": 0.35},
    "Espinaca":  {"pest": "Mildiú (downy mildew)", "moist_high": 0.35},
    "Brócoli":   {"pest": "Polilla dorso de diamante (DBM)", "moist_high": 0.45},
    "Coliflor":  {"pest": "Polilla dorso de diamante (DBM)", "moist_high": 0.45},
    "Fresa":     {"pest": "Botrytis (moho gris)", "moist_high": 0.40},
    "Apio":      {"pest": "Tizón tardío (late blight)", "moist_high": 0.38},
    "Alcachofa": {"pest": "Pulgón / Botrytis", "moist_high": 0.40},
}

_DEFAULT = {"pest": "Plagas generales", "moist_high": 0.40}


def pest_risk(crop_type: str | None, ndmi: float | None) -> dict:
    """Return ``{pest, level, score, note, is_demo}`` for a crop + moisture proxy.

    *ndmi* is the field's latest NDMI mean (canopy moisture). Higher moisture →
    higher fungal risk. When NDMI is unknown the risk defaults to ``bajo``.
    """
    info = _CROP_THREATS.get((crop_type or "").strip(), _DEFAULT)
    if ndmi is None:
        level, score = "bajo", 15
    else:
        high = info["moist_high"]
        if ndmi >= high + 0.08:
            level, score = "alto", 80
        elif ndmi >= high:
            level, score = "medio", 50
        else:
            level, score = "bajo", 20
    return {
        "is_demo": True,
        "pest": info["pest"],
        "level": level,
        "score": score,
        "note": "Riesgo estimado (demo) por cultivo y humedad foliar NDMI.",
    }
