"""Pest & disease risk model for coastal vegetables — driven by REAL data.

Combines the crop's primary threat with live weather (temperature, relative
humidity, recent rain → canopy wetness) and the latest satellite canopy moisture
(NDMI). This is a simplified agronomic model (temperature × leaf-wetness windows),
not a static demo: the inputs are real Open-Meteo + satellite values.

Calibration with growers' scouting records turns these windows into fitted
thresholds (see docs/SATELLITES roadmap).
"""

from __future__ import annotations

# Per coastal-vegetable crop: primary threat + favourable temp window (°C) and the
# relative-humidity threshold (%) above which infection pressure rises. ``kind``
# distinguishes fungal (needs leaf wetness) from insect (degree-day driven).
CROP_THREAT: dict[str, dict] = {
    "Lechuga":   {"pest": "Mildiú velloso (downy mildew)", "t": (10, 20), "rh": 85, "kind": "fungal"},
    "Espinaca":  {"pest": "Mildiú (downy mildew)",          "t": (8, 18),  "rh": 85, "kind": "fungal"},
    "Brócoli":   {"pest": "Polilla dorso de diamante (DBM)", "t": (20, 30), "rh": 55, "kind": "insect"},
    "Coliflor":  {"pest": "Polilla dorso de diamante (DBM)", "t": (20, 30), "rh": 55, "kind": "insect"},
    "Fresa":     {"pest": "Botrytis (moho gris)",            "t": (15, 25), "rh": 85, "kind": "fungal"},
    "Apio":      {"pest": "Tizón tardío (late blight)",      "t": (15, 22), "rh": 88, "kind": "fungal"},
    "Alcachofa": {"pest": "Pulgón / Botrytis",               "t": (15, 25), "rh": 80, "kind": "fungal"},
    "Vid":       {"pest": "Oídio (Erysiphe necator)",        "t": (18, 27), "rh": 70, "kind": "fungal"},
}
_DEFAULT = {"pest": "Plagas/enfermedades generales", "t": (12, 26), "rh": 80, "kind": "fungal"}


def pest_risk_model(
    crop_type: str | None,
    temp_c: float | None,
    humidity_pct: float | None,
    recent_rain_mm: float | None,
    ndmi: float | None,
) -> dict:
    """Compute a 0–100 pest/disease risk from real weather + satellite moisture.

    Drivers are listed so the agronomist sees *why*. Returns level bajo/medio/alto,
    score, drivers and a recommendation.
    """
    info = CROP_THREAT.get((crop_type or "").strip(), _DEFAULT)
    score = 0
    drivers: list[str] = []

    # Temperature window (weight 35)
    if temp_c is not None:
        lo, hi = info["t"]
        if lo <= temp_c <= hi:
            score += 35
            drivers.append(f"Temperatura en ventana del patógeno ({temp_c:.0f} °C)")
        elif lo - 3 <= temp_c <= hi + 3:
            score += 15

    # Humidity (weight 35 for fungal, less for insects)
    w_rh = 35 if info["kind"] == "fungal" else 20
    if humidity_pct is not None and humidity_pct >= info["rh"]:
        score += w_rh
        drivers.append(f"Humedad relativa alta ({humidity_pct:.0f} %)")

    # Canopy wetness: recent rain or high NDMI (weight 30 fungal / 15 insect)
    wet = (recent_rain_mm or 0) >= 2 or (ndmi is not None and ndmi >= 0.4)
    if wet and info["kind"] == "fungal":
        score += 30
        drivers.append("Follaje húmedo (lluvia reciente o NDMI alto)")
    elif info["kind"] == "insect" and temp_c is not None and temp_c >= info["t"][0]:
        score += 25
        drivers.append("Temperaturas cálidas favorecen el desarrollo del insecto")

    score = min(100, score)
    level = "alto" if score >= 70 else "medio" if score >= 40 else "bajo"

    rec = {
        "alto": f"Condiciones muy favorables para {info['pest']}. Inspeccionar hoy y considerar control preventivo.",
        "medio": f"Riesgo moderado de {info['pest']}. Monitorear de cerca las próximas 48 h.",
        "bajo": f"Bajo riesgo de {info['pest']}. Mantener monitoreo de rutina.",
    }[level]

    return {
        "is_model": True,
        "pest": info["pest"],
        "level": level,
        "score": score,
        "drivers": drivers or ["Sin condiciones de riesgo destacadas"],
        "recommendation": rec,
    }


# ── Deep multi-pest assessment (catalog-driven) ─────────────────────────────────

def _level(score: int) -> str:
    return "alto" if score >= 70 else "medio" if score >= 40 else "bajo"


def assess_pest(
    pest: dict,
    temp_c: float | None,
    humidity: float | None,
    wet_hours: float | None,
    degree_days: float | None,
    stage: str | None = None,
) -> dict:
    """Risk for one catalog pest from real weather metrics + phenology.

    - Fungal: temperature window + relative humidity + leaf-wetness hours (RH≥90).
    - Insect: accumulated degree-days (development) + current temperature window.
    """
    score = 0
    drivers: list[str] = []
    lo, hi = pest.get("temp_opt", (12, 26))
    in_window = temp_c is not None and lo <= temp_c <= hi

    if pest.get("kind") == "fungal":
        if in_window:
            score += 30
            drivers.append(f"Temperatura favorable ({temp_c:.0f} °C)")
        rh_min = pest.get("rh_min", 85)
        if humidity is not None and humidity >= rh_min:
            score += 30
            drivers.append(f"Humedad relativa alta ({humidity:.0f} %)")
        need = pest.get("wet_hours", 4)
        if need and wet_hours is not None and wet_hours >= need:
            score += 40
            drivers.append(f"Humedad foliar suficiente ({wet_hours:.0f} h ≥ {need} h)")
        elif need == 0 and humidity is not None and humidity >= rh_min:
            score += 20  # oídio: no necesita agua libre
    else:  # insect — degree-day driven
        base = pest.get("dd_base", 8)
        thr = pest.get("dd_threshold", 150)
        if degree_days is not None and thr:
            ratio = degree_days / thr
            if ratio >= 1.0:
                score += 50
                drivers.append(f"Acumulación térmica alta ({degree_days:.0f} GD ≥ {thr}, base {base} °C)")
            elif ratio >= 0.6:
                score += 30
                drivers.append(f"Acumulación térmica en aumento ({degree_days:.0f}/{thr} GD)")
        if in_window:
            score += 35
            drivers.append(f"Temperatura óptima para el insecto ({temp_c:.0f} °C)")

    score = min(100, score)
    level = _level(score)
    rec = {
        "alto": f"Condiciones muy favorables para {pest['name']}. Inspeccionar HOY: {pest.get('scout','')}",
        "medio": f"Riesgo moderado de {pest['name']}. Monitorear 48 h. {pest.get('scout','')}",
        "bajo": f"Bajo riesgo de {pest['name']}. Monitoreo de rutina.",
    }[level]
    return {
        "name": pest["name"], "kind": pest.get("kind"), "level": level, "score": score,
        "drivers": drivers or ["Sin condiciones de riesgo destacadas"],
        "scout": pest.get("scout", ""), "recommendation": rec,
    }


def assess_pests(active: list[dict], temp_c, humidity, wet_hours, degree_days, stage=None) -> list[dict]:
    """Assess a list of catalog pest dicts; returns them sorted by risk (high→low)."""
    out = [assess_pest(p, temp_c, humidity, wet_hours, degree_days, stage) for p in active]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
