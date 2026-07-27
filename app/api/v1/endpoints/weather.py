"""Weather data proxy — Open-Meteo (free, no key required)."""

import uuid

import httpx
from fastapi import APIRouter, HTTPException
from geoalchemy2.functions import ST_AsText, ST_Centroid
from shapely import wkt as shapely_wkt
from sqlalchemy import select

from app.api.deps import CurrentUser, DBSession
from app.models.field import Field

router = APIRouter()

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
_DAILY_VARS = ",".join([
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "et0_fao_evapotranspiration",
    "shortwave_radiation_sum",
    "windspeed_10m_max",
])
_HOURLY_VARS = "soil_temperature_0cm,soil_moisture_0_to_1cm"
_CURRENT_VARS = ",".join([
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "windspeed_10m",
    "weathercode",
    "apparent_temperature",
])


@router.get("/{field_id}/weather")
async def get_field_weather(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> dict:
    """Return 30-day weather history + 7-day forecast for the field centroid."""
    result = await db.execute(
        select(ST_AsText(ST_Centroid(Field.geometry))).where(
            Field.id == field_id, Field.user_id == current_user.id
        )
    )
    centroid_wkt = result.scalar_one_or_none()
    if not centroid_wkt:
        raise HTTPException(status_code=404, detail="Field not found.")

    pt = shapely_wkt.loads(centroid_wkt)
    lat = round(pt.y, 6)
    # Normalize longitude to [-180, 180]
    lon = round(((pt.x + 180) % 360) - 180, 6)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Field geometry has invalid coordinates.")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _OPEN_METEO,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": _DAILY_VARS,
                    "hourly": _HOURLY_VARS,
                    "current": _CURRENT_VARS,
                    "past_days": 30,
                    "forecast_days": 7,
                    "timezone": "auto",
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="El servicio meteorológico no respondió. Intenta de nuevo en unos segundos.") from None
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Error de conexión al servicio meteorológico.") from exc

    if resp.status_code != 200:
        from loguru import logger
        logger.error(f"Open-Meteo error {resp.status_code}: {resp.text[:400]}")
        raise HTTPException(status_code=502, detail=f"Weather service error: {resp.text[:300]}")

    data = resp.json()

    # Aggregate hourly soil data to daily (take midday value: hour 12 of each day)
    hourly = data.get("hourly", {})
    hourly_times = hourly.get("time", [])
    soil_temp_h = hourly.get("soil_temperature_0cm", [])
    soil_moist_h = hourly.get("soil_moisture_0_to_1cm", [])

    # Pick one value per day (noon = index 12, 36, 60, … within each 24h block)
    soil_temp_daily, soil_moist_daily = [], []
    for i in range(0, len(hourly_times), 24):
        noon = i + 12
        soil_temp_daily.append(soil_temp_h[noon] if noon < len(soil_temp_h) else None)
        soil_moist_daily.append(soil_moist_h[noon] if noon < len(soil_moist_h) else None)

    data["daily"]["soil_temperature_0_to_7cm"] = soil_temp_daily
    data["daily"]["soil_moisture_0_to_7cm"] = soil_moist_daily
    data.pop("hourly", None)

    data["field_id"] = str(field_id)
    data["centroid"] = {"lat": lat, "lon": lon}
    return data


@router.get("/{field_id}/climate-forecast")
async def climate_forecast(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> dict:
    """
    Return monthly climate normals (20-year avg) + seasonal forecast for the rest of the year.
    Uses Open-Meteo Climate API (free, no key required).
    """
    import datetime

    from shapely.wkt import loads as wkt_loads

    result = await db.execute(
        select(
            ST_AsText(ST_Centroid(Field.geometry)).label("centroid")
        ).where(Field.id == field_id, Field.user_id == current_user.id)
    )
    row = result.mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Field not found.")

    point = wkt_loads(row["centroid"])
    lat, lon = point.y, point.x

    today = datetime.date.today()
    # 5-year historical normals (shorter range keeps response fast)
    start_year = today.year - 5
    end_year = today.year - 1

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            climate_resp = await client.get(
                "https://climate-api.open-meteo.com/v1/climate",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": f"{start_year}-01-01",
                    "end_date": f"{end_year}-12-31",
                    "models": "CMCC_CM2_VHR4",
                    "daily": "temperature_2m_mean,precipitation_sum,et0_fao_evapotranspiration",
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=503, detail="El servicio climático no respondió.") from None
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="Error de conexión al servicio climático.") from exc

    if climate_resp.status_code != 200:
        from loguru import logger
        logger.error(f"Climate API error {climate_resp.status_code}: {climate_resp.text[:400]}")
        raise HTTPException(502, f"Climate API error: {climate_resp.text[:200]}")

    climate_data = climate_resp.json()
    monthly = climate_data.get("daily", {})

    # Build monthly normals (avg per calendar month 1-12)
    from collections import defaultdict
    temp_by_month: dict = defaultdict(list)
    rain_by_month: dict = defaultdict(list)
    et0_by_month: dict = defaultdict(list)

    times = monthly.get("time", [])
    temps = monthly.get("temperature_2m_mean", [])
    rains = monthly.get("precipitation_sum", [])
    et0s = monthly.get("et0_fao_evapotranspiration", [])

    for i, t in enumerate(times):
        m = int(t[5:7])
        if i < len(temps) and temps[i] is not None:
            temp_by_month[m].append(temps[i])
        if i < len(rains) and rains[i] is not None:
            rain_by_month[m].append(rains[i])
        if i < len(et0s) and et0s[i] is not None:
            et0_by_month[m].append(et0s[i])

    month_names = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    normals = []
    for m in range(1, 13):
        normals.append({
            "month": m,
            "month_name": month_names[m - 1],
            "temp_mean": round(sum(temp_by_month[m]) / len(temp_by_month[m]), 1) if temp_by_month[m] else None,
            "precip_sum": round(sum(rain_by_month[m]) / len(rain_by_month[m]), 1) if rain_by_month[m] else None,
            "et0_sum": round(sum(et0_by_month[m]) / len(et0_by_month[m]), 1) if et0_by_month[m] else None,
        })

    return {
        "field_id": str(field_id),
        "lat": lat,
        "lon": lon,
        "normals_years": f"{start_year}–{end_year}",
        "normals": normals,
        "current_month": today.month,
        "remaining_months": list(range(today.month, 13)),
    }
