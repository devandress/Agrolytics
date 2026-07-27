"""Daily agronomic advisory based on Open-Meteo weather data.

No satellite data required — works from day 1 for any field.
Generates irrigation, frost, and spray-window recommendations.
"""

import uuid
from datetime import date

import httpx
from fastapi import APIRouter, HTTPException
from geoalchemy2.functions import ST_AsText, ST_Centroid
from shapely import wkt as shapely_wkt
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field
from app.services.irrigation import daily_water_need
from app.services.phenology import current_stage

router = APIRouter()

# ET0 thresholds per crop (mm/day above which irrigation is likely needed)
_CROP_THRESHOLDS: dict[str, dict] = {
    "WHEAT":    {"frost_c": 0,  "heat_c": 32, "et0_factor": 0.9},
    "CORN":     {"frost_c": 2,  "heat_c": 38, "et0_factor": 1.1},
    "SOYBEAN":  {"frost_c": 2,  "heat_c": 36, "et0_factor": 1.05},
    "RICE":     {"frost_c": 10, "heat_c": 40, "et0_factor": 1.2},
    "TOMATO":   {"frost_c": 3,  "heat_c": 35, "et0_factor": 1.1},
    "POTATO":   {"frost_c": 1,  "heat_c": 30, "et0_factor": 1.0},
    "SUNFLOWER":{"frost_c": 2,  "heat_c": 38, "et0_factor": 1.0},
    "ALFALFA":  {"frost_c": -3, "heat_c": 38, "et0_factor": 1.15},
    # Wine grapes: budbreak frost (≤1 °C) is a major spring risk; Kc handled by the
    # stage-aware irrigation service (et0_factor kept only as a legacy fallback).
    "VID":      {"frost_c": 1,  "heat_c": 38, "et0_factor": 0.7},
}
_DEFAULT_THRESHOLDS = {"frost_c": 2, "heat_c": 36, "et0_factor": 1.0}


@router.get("/{field_id}/advisory")
async def daily_advisory(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> dict:
    """Return today's agronomic advisory for a field.

    Includes: irrigation recommendation, frost risk, heat stress, spray window,
    and a 3-day outlook — all derived from real-time Open-Meteo data.
    """
    result = await db.execute(
        select(
            ST_AsText(ST_Centroid(Field.geometry)).label("centroid"),
            Field.crop_type,
            Field.name,
            Field.area_ha,
            Field.planting_date,
        ).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Field not found.")

    pt = shapely_wkt.loads(row["centroid"])
    lat = round(pt.y, 6)
    lon = round(((pt.x + 180) % 360) - 180, 6)
    crop_type_raw = row["crop_type"]            # original case — for Kc/phenology lookups
    crop = (crop_type_raw or "").upper()        # uppercased — for thresholds/display
    thresholds = _CROP_THRESHOLDS.get(crop, _DEFAULT_THRESHOLDS)
    # Stage-aware crop water demand needs the current phenology stage (no satellite
    # required — derived from the calendar season for vines, planting date for annuals).
    stage = current_stage(crop_type_raw, row["planting_date"])

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": ",".join([
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_sum",
                        "et0_fao_evapotranspiration",
                        "windspeed_10m_max",
                        "precipitation_probability_max",
                    ]),
                    "hourly": "relative_humidity_2m,windspeed_10m",
                    "current": "temperature_2m,precipitation,windspeed_10m,relative_humidity_2m,weathercode",
                    "past_days": 1,
                    "forecast_days": 7,
                    "timezone": "auto",
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="El servicio meteorológico no respondió. Intenta de nuevo en unos segundos.") from None
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Error de conexión al servicio meteorológico.") from exc

    if resp.status_code != 200:
        raise HTTPException(502, "Weather service unavailable.")

    w = resp.json()
    daily = w.get("daily", {})
    current = w.get("current", {})

    times = daily.get("time", [])
    today_idx = 1  # index 0 = yesterday, 1 = today (with past_days=1)

    def _get(key: str, idx: int):
        vals = daily.get(key, [])
        return vals[idx] if idx < len(vals) else None

    today = date.today().isoformat()
    # Find the index matching today's date
    for i, t in enumerate(times):
        if t == today:
            today_idx = i
            break

    tmax = _get("temperature_2m_max", today_idx)
    tmin = _get("temperature_2m_min", today_idx)
    rain = _get("precipitation_sum", today_idx) or 0.0
    et0  = _get("et0_fao_evapotranspiration", today_idx) or 0.0
    wind = _get("windspeed_10m_max", today_idx) or 0.0
    rain_prob = _get("precipitation_probability_max", today_idx) or 0

    # ── Irrigation recommendation (stage-aware Kc + effective rainfall) ───────
    wb = daily_water_need(et0, crop_type_raw, stage, rain)
    net = wb["net_irrigation_mm"]
    water_deficit = net  # reused by the today{} block and the summary badge below
    if wb["rdi"]:
        irrigation = {
            "needed": net > 4,
            "urgency": "media" if net > 4 else "baja",
            "dose_mm": net,
            "message": (
                f"Riego deficitario controlado (RDI): la vid está en maduración y un déficit "
                f"moderado favorece la calidad. ETc objetivo {wb['target_etc_mm']} mm; "
                f"riego sugerido {net} mm — no forzar por encima."
            ),
        }
    elif net > 3:
        irrigation = {
            "needed": True,
            "urgency": "alta" if net > 6 else "media",
            "dose_mm": net,
            "message": (
                f"Necesidad hídrica neta de {net} mm (ETc {wb['etc_mm']} mm con Kc {wb['kc']}, "
                f"menos lluvia efectiva {wb['effective_rain_mm']} mm)."
            ),
        }
    elif net > 0:
        irrigation = {
            "needed": False, "urgency": "baja", "dose_mm": 0,
            "message": f"Déficit leve ({net} mm). Monitorear; riego preventivo opcional.",
        }
    else:
        irrigation = {
            "needed": False, "urgency": "ninguna", "dose_mm": 0,
            "message": f"Sin déficit: ETc {wb['etc_mm']} mm cubierto por la lluvia efectiva ({wb['effective_rain_mm']} mm).",
        }
    irrigation["net_irrigation_mm"] = net
    irrigation["rdi"] = wb["rdi"]

    # ── Frost risk ────────────────────────────────────────────────────────────
    frost_threshold = thresholds["frost_c"]
    frost_days = []
    for i in range(today_idx, min(today_idx + 4, len(times))):
        t_min = _get("temperature_2m_min", i)
        if t_min is not None and t_min <= frost_threshold:
            frost_days.append({"date": times[i], "tmin": t_min})

    frost = {
        "risk": len(frost_days) > 0,
        "days": frost_days,
        "message": (
            f"ALERTA: Helada probable en {len(frost_days)} día(s). Temp. mín. ≤ {frost_threshold}°C para {crop or 'este cultivo'}."
            if frost_days else "Sin riesgo de helada en los próximos 3 días."
        ),
    }

    # ── Heat stress ───────────────────────────────────────────────────────────
    heat_threshold = thresholds["heat_c"]
    heat = {
        "risk": tmax is not None and tmax >= heat_threshold,
        "tmax": tmax,
        "message": (
            f"ALERTA: Estrés térmico probable (máx {tmax}°C ≥ {heat_threshold}°C). Considerar riego de alivio."
            if tmax and tmax >= heat_threshold
            else f"Sin estrés térmico (máx {tmax}°C)."
        ),
    }

    # ── Spray window (next 48h with no rain + wind < 20 km/h) ─────────────────
    spray_windows = []
    for i in range(today_idx, min(today_idx + 3, len(times))):
        r = _get("precipitation_sum", i) or 0
        rp = _get("precipitation_probability_max", i) or 0
        w_max = _get("windspeed_10m_max", i) or 99
        if r < 1 and rp < 30 and w_max < 20:
            spray_windows.append({"date": times[i], "rain_mm": r, "wind_max": w_max})

    spray = {
        "has_window": len(spray_windows) > 0,
        "windows": spray_windows,
        "message": (
            f"Ventana de aplicación disponible: {', '.join(d['date'] for d in spray_windows)}."
            if spray_windows else "No hay ventana favorable para pulverización en los próximos 3 días (lluvia o viento)."
        ),
    }

    # ── 3-day outlook ─────────────────────────────────────────────────────────
    outlook = []
    for i in range(today_idx, min(today_idx + 4, len(times))):
        outlook.append({
            "date": times[i],
            "tmax": _get("temperature_2m_max", i),
            "tmin": _get("temperature_2m_min", i),
            "rain_mm": _get("precipitation_sum", i),
            "rain_prob_pct": _get("precipitation_probability_max", i),
            "et0": _get("et0_fao_evapotranspiration", i),
            "wind_max": _get("windspeed_10m_max", i),
        })

    # ── Summary badge ─────────────────────────────────────────────────────────
    alerts = []
    if frost["risk"]:
        alerts.append("helada")
    if heat["risk"]:
        alerts.append("calor extremo")
    if irrigation["needed"] and irrigation["urgency"] == "alta":
        alerts.append("riego urgente")

    return {
        "field_id": str(field_id),
        "field_name": row["name"],
        "crop": crop,
        "date": today,
        "lat": lat,
        "lon": lon,
        "current": current,
        "today": {
            "tmax": tmax,
            "tmin": tmin,
            "rain_mm": rain,
            "et0": round(et0, 1),
            "kc": wb["kc"],
            "etc_mm": wb["etc_mm"],
            "effective_rain_mm": wb["effective_rain_mm"],
            "stage": stage,
            "water_deficit_mm": water_deficit,
            "wind_max_kmh": wind,
            "rain_probability_pct": rain_prob,
        },
        "irrigation": irrigation,
        "frost": frost,
        "heat": heat,
        "spray": spray,
        "outlook": outlook,
        "alerts": alerts,
        "alert_count": len(alerts),
    }
