"""Phenology / phenotype tracking from the NDVI time-series + planting date.

Gives the farmer the crop's developmental story over time: growth stage, days
since planting, and how the observed NDVI compares to a typical (expected) curve
for that crop — a simple phenotype/vigour read. All from real satellite NDVI.
"""

from __future__ import annotations

from datetime import date

# Days-to-harvest per coastal crop (cycle length) for the stage timeline.
_CYCLE_DAYS: dict[str, int] = {
    "Lechuga": 75, "Espinaca": 45, "Brócoli": 90, "Coliflor": 95,
    "Fresa": 120, "Apio": 120, "Alcachofa": 150,
    "Vid": 213,  # perennial: budbreak→harvest season length (see vine handling below)
}
# Typical peak NDVI reached by a healthy crop near canopy closure.
_PEAK_NDVI: dict[str, float] = {
    "Lechuga": 0.78, "Espinaca": 0.75, "Brócoli": 0.82, "Coliflor": 0.80,
    "Fresa": 0.72, "Apio": 0.80, "Alcachofa": 0.78,
    "Vid": 0.62,  # vines run lower (woody canopy + inter-row mixed in 10 m pixels)
}
_DEFAULT_CYCLE = 90
_DEFAULT_PEAK = 0.78

STAGES = [
    (0.00, "establecimiento"),
    (0.25, "crecimiento vegetativo"),
    (0.60, "llenado / madurez"),
    (0.90, "lista para cosecha"),
]

# ── Perennial vines ─────────────────────────────────────────────────────────────
# Vines aren't driven by a planting date but by the annual season. Northern-
# Hemisphere defaults (Baja California / California): budbreak ~mid-March, harvest
# ~mid-October. Outside that window the vine is dormant.
_VINE_NAMES = {"vid", "uva", "grape", "vineyard"}
_VINE_BUDBREAK_DOY = 75    # ~Mar 16
_VINE_HARVEST_DOY = 288    # ~Oct 15


def is_vine(crop_type: str | None) -> bool:
    return (crop_type or "").strip().lower() in _VINE_NAMES


def vine_progress(today: date) -> float | None:
    """Season fraction 0..1 from budbreak to harvest; None when dormant."""
    doy = today.timetuple().tm_yday
    if doy < _VINE_BUDBREAK_DOY or doy > _VINE_HARVEST_DOY:
        return None
    return (doy - _VINE_BUDBREAK_DOY) / (_VINE_HARVEST_DOY - _VINE_BUDBREAK_DOY)


def current_stage(crop_type: str | None, planting_date: date | None,
                  today: date | None = None) -> str:
    """Best-effort phenology stage without satellite data (for weather advisories).

    Vines use the calendar season; annuals use days-since-planting. Falls back to
    the peak-demand stage when an annual has no planting date recorded.
    """
    today = today or date.today()
    if is_vine(crop_type):
        p = vine_progress(today)
        return "dormancia" if p is None else stage_for(p)
    if planting_date:
        cycle = _CYCLE_DAYS.get((crop_type or "").strip(), _DEFAULT_CYCLE)
        return stage_for(max(0.0, min(1.0, (today - planting_date).days / cycle)))
    return "llenado / madurez"  # conservative: assume peak water demand


def stage_for(pct: float) -> str:
    label = STAGES[0][1]
    for thr, name in STAGES:
        if pct >= thr:
            label = name
    return label


def expected_ndvi(crop: str | None, pct: float) -> float:
    """Typical NDVI at a given fraction of the cycle — a rise-then-plateau curve."""
    peak = _PEAK_NDVI.get((crop or "").strip(), _DEFAULT_PEAK)
    if pct <= 0.6:
        # linear rise from ~0.2 to peak by canopy closure (~60% of cycle)
        return round(0.2 + (peak - 0.2) * (pct / 0.6), 3)
    if pct <= 0.9:
        return round(peak, 3)
    # gentle senescence toward harvest
    return round(peak - (peak - 0.45) * ((pct - 0.9) / 0.1), 3)


def assess_phenology(
    ndvi_series: list[tuple[date, float]],
    crop_type: str | None,
    planting_date: date | None,
) -> dict:
    """Return stage, days since planting, expected vs observed NDVI and vigour."""
    pts = sorted([(d, float(v)) for d, v in ndvi_series if v is not None], key=lambda x: x[0])
    current = pts[-1][1] if pts else None

    cycle = _CYCLE_DAYS.get((crop_type or "").strip(), _DEFAULT_CYCLE)
    if is_vine(crop_type):
        # Perennial: driven by the calendar season, not a planting date.
        p = vine_progress(date.today())
        if p is None:
            days, pct, stage, exp = None, None, "dormancia (fuera de temporada)", None
        else:
            days, pct = None, p
            stage = stage_for(p)
            exp = expected_ndvi(crop_type, p)
    elif planting_date:
        days = (date.today() - planting_date).days
        pct = max(0.0, min(1.2, days / cycle))
        stage = stage_for(min(1.0, pct))
        exp = expected_ndvi(crop_type, min(1.0, pct))
    else:
        days, pct, stage, exp = None, None, "desconocida (sin fecha de siembra)", None

    vigor = None
    delta = None
    if current is not None and exp is not None:
        delta = round(current - exp, 3)
        vigor = "por encima de lo esperado" if delta > 0.05 else (
            "por debajo de lo esperado" if delta < -0.05 else "según lo esperado")

    # Build the expected-vs-observed curve for the chart/table.
    curve = []
    if is_vine(crop_type):
        for d, v in pts:
            p = vine_progress(d)
            if p is not None:
                curve.append({"date": str(d), "ndvi": round(v, 3),
                              "expected": expected_ndvi(crop_type, p), "days": d.timetuple().tm_yday})
    elif planting_date:
        for d, v in pts:
            dd = (d - planting_date).days
            p = max(0.0, min(1.0, dd / cycle))
            curve.append({"date": str(d), "ndvi": round(v, 3), "expected": expected_ndvi(crop_type, p),
                          "days": dd})

    return {
        "crop": crop_type, "cycle_days": cycle, "days_since_planting": days,
        "progress_pct": round(pct * 100) if pct is not None else None,
        "stage": stage, "current_ndvi": round(current, 3) if current is not None else None,
        "expected_ndvi": exp, "ndvi_delta": delta, "vigor": vigor,
        "curve": curve, "n_obs": len(pts),
    }
