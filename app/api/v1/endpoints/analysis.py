"""Multi-sensor analysis API: time-series, raster layers, normalised render, fusion.

Powers the analysis workbench (multi-sensor chart + interactive layer/swipe viewer).
"""

import asyncio
import json
import uuid
from datetime import date, timedelta
from datetime import date as _date
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from geoalchemy2.functions import ST_AsGeoJSON, ST_AsText, ST_Centroid
from pydantic import BaseModel
from shapely import wkt as shapely_wkt
from sqlalchemy import and_, select

from app.api.deps import AiUser, CurrentUser, DBSession
from app.core.config import settings
from app.core.limiter import limiter
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.index import Index
from app.services import fusion
from app.services.ai_tasks import propose_tasks
from app.services.biomass import estimate_biomass
from app.services.pest_catalog import PEST_CATALOG, default_pests_for_crop
from app.services.pest_model import assess_pests, forecast_pest, pest_risk_model
from app.services.pest_risk_raster import build_pest_risk_raster
from app.services.phenology import assess_phenology
from app.services.raster_renderer import INDEX_SCALE, render_to_png
from app.services.sensors import BACKBONE_KEY, REGISTRY

router = APIRouter()


class ProposeBody(BaseModel):
    messages: list[dict] | None = None


class NewTask(BaseModel):
    task_type: str = "otro"
    title: str
    detail: str | None = None
    priority: int = 3
    recommended_value: str | None = None
    lat: float | None = None
    lon: float | None = None


async def _own(db, field_id, user_id):
    r = await db.execute(select(Field.id).where(Field.id == field_id, Field.user_id == user_id))
    if not r.scalar_one_or_none():
        raise HTTPException(404, "Campo no encontrado.")


def _require_index_plan(current_user, index: str) -> None:
    """Server-side plan gate — the free plan's index list (config.py) is only
    real if it's enforced here, not just displayed in the billing UI."""
    from app.services.plans import plan_allows_index
    if not plan_allows_index(current_user.plan, index):
        raise HTTPException(
            status_code=402,
            detail=f"El índice {index.upper()} no está incluido en tu plan. Mejora tu plan para verlo.",
        )


def _sensor_of(meta: dict | None) -> str:
    if meta and meta.get("sensor"):
        return meta["sensor"]
    return "s2"  # legacy rows from the original Sentinel-2 pipeline


def _res_of(meta: dict | None, sensor_key: str) -> int:
    if meta and meta.get("native_res_m"):
        return int(meta["native_res_m"])
    s = REGISTRY.get(sensor_key)
    return s.native_res_m if s else 10


@router.get("/{field_id}/analysis/timeseries")
async def timeseries(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                     index: str = Query("NDVI"), days: int = Query(365, ge=1, le=1825)) -> dict[str, Any]:
    """Unified multi-sensor index time-series; each point tagged with its sensor."""
    await _own(db, field_id, current_user.id)
    _require_index_plan(current_user, index)
    since = date.today() - timedelta(days=days)
    rows = (await db.execute(
        select(Index).where(and_(Index.field_id == field_id, Index.index_type == index.upper(),
                                  Index.mean_value.isnot(None), Index.date >= since))
        .order_by(Index.date.asc()))).scalars().all()
    series = []
    sensors_seen = set()
    for r in rows:
        sk = _sensor_of(r.extra_meta)
        sensors_seen.add(sk)
        series.append({"date": str(r.date), "value": round(r.mean_value, 4), "sensor": sk,
                       "res_m": _res_of(r.extra_meta, sk), "kind": "observado"})
    return {"index": index.upper(), "series": series,
            "sensors": sorted(sensors_seen), "scale": INDEX_SCALE.get(index.upper())}


@router.get("/{field_id}/analysis/layers")
async def layers(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                 index: str = Query("NDVI")) -> list[dict[str, Any]]:
    """Available raster layers (date + sensor) for the interactive viewer."""
    await _own(db, field_id, current_user.id)
    _require_index_plan(current_user, index)
    rows = (await db.execute(
        select(Index).where(and_(Index.field_id == field_id, Index.index_type == index.upper(),
                                 Index.raster_uri.isnot(None)))
        .order_by(Index.date.desc()))).scalars().all()
    out = []
    for r in rows:
        sk = _sensor_of(r.extra_meta)
        out.append({
            "date": str(r.date), "sensor": sk, "res_m": _res_of(r.extra_meta, sk),
            "value": round(r.mean_value, 4) if r.mean_value is not None else None,
            "url": f"/api/v1/fields/{field_id}/analysis/render?index={index.upper()}&date={r.date}&sensor={sk}",
        })
    return out


@router.get("/{field_id}/analysis/render")
async def render(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                 index: str = Query("NDVI"), date_str: str = Query(..., alias="date"),
                 sensor: str = Query("s2")) -> Response:
    """Normalised RGBA PNG for a given date+sensor; bounds in X-Raster-Bounds header."""
    await _own(db, field_id, current_user.id)
    _require_index_plan(current_user, index)
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(400, "Fecha inválida (YYYY-MM-DD).") from None
    rows = (await db.execute(
        select(Index.raster_uri, Index.extra_meta).where(
            and_(Index.field_id == field_id, Index.index_type == index.upper(), Index.date == d,
                 Index.raster_uri.isnot(None))))).all()
    uri = None
    for raster_uri, meta in rows:
        if _sensor_of(meta) == sensor:
            uri = raster_uri
            break
    if uri is None and rows:
        uri = rows[0][0]
    if not uri or not Path(uri).exists():
        raise HTTPException(404, "Ráster no disponible para esa fecha/sensor.")
    # Raster IO + colormap are sync CPU/disk work — run off the event loop.
    geo = await _geom_of(db, field_id)
    png, (w, s, e, n) = await asyncio.to_thread(render_to_png, uri, 255, index.upper(), geo)
    return Response(content=png, media_type="image/png",
                    headers={"X-Raster-Bounds": f"{w},{s},{e},{n}", "Cache-Control": "no-store"})


async def _crop_of(db, field_id) -> str | None:
    return (await db.execute(select(Field.crop_type).where(Field.id == field_id))).scalar_one_or_none()


async def _geom_of(db, field_id) -> dict | None:
    g = (await db.execute(select(ST_AsGeoJSON(Field.geometry)).where(Field.id == field_id))).scalar_one_or_none()
    return json.loads(g) if g else None


@router.get("/{field_id}/pest-risk/layer")
async def pest_risk_layer(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> list[dict[str, Any]]:
    """Pest-risk raster as a single map layer (built on demand from NDVI+NDMI)."""
    await _own(db, field_id, current_user.id)
    res = await build_pest_risk_raster(db, field_id, await _crop_of(db, field_id), settings.DATA_DIR)
    if not res:
        return []
    return [{"date": res["date"], "sensor": "risk", "res_m": 10, "kind": "riesgo",
             "zones": res["zones"], "pest": res["pest"],
             "url": f"/api/v1/fields/{field_id}/pest-risk/render"}]


@router.get("/{field_id}/pest-risk/render")
async def pest_risk_render(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> Response:
    """PNG of the pest-risk raster (green→red); bounds in X-Raster-Bounds header."""
    await _own(db, field_id, current_user.id)
    res = await build_pest_risk_raster(db, field_id, await _crop_of(db, field_id), settings.DATA_DIR)
    if not res or not Path(res["path"]).exists():
        raise HTTPException(404, "Aún no hay datos satelitales para calcular el riesgo.")
    geo = await _geom_of(db, field_id)
    png, (w, s, e, n) = await asyncio.to_thread(render_to_png, res["path"], 255, "PESTRISK", geo)
    return Response(content=png, media_type="image/png",
                    headers={"X-Raster-Bounds": f"{w},{s},{e},{n}", "Cache-Control": "no-store"})


async def _read_grid(db, field_id, index: str) -> tuple[list | None, list | None]:
    """Downsampled (≤~80×80) value grid + WGS84 bounds for a field's index raster.

    Returns ``(values2d, [w, s, e, n])`` or ``(None, None)`` when no raster exists.
    """
    idx = index.upper()
    if idx == "RIESGO":
        res = await build_pest_risk_raster(db, field_id, await _crop_of(db, field_id), settings.DATA_DIR)
        uri = res["path"] if res else None
    else:
        row = (await db.execute(select(Index.raster_uri).where(
            and_(Index.field_id == field_id, Index.index_type == idx, Index.raster_uri.isnot(None)))
            .order_by(Index.date.desc()).limit(1))).first()
        uri = row[0] if row else None
    if not uri or not Path(uri).exists():
        return None, None

    geo = await _geom_of(db, field_id)

    def _read_sync():
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.warp import transform_bounds, transform_geom
        with rasterio.open(uri) as src:
            arr = src.read(1).astype("float32")
            crs, tr = src.crs, src.transform
            w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        if geo:
            try:
                outside = geometry_mask([transform_geom("EPSG:4326", crs, geo)],
                                        transform=tr, invert=False, out_shape=arr.shape)
                arr2 = np.where(outside, np.nan, arr)
            except Exception:
                arr2 = arr
        else:
            arr2 = arr
        step = max(1, max(arr2.shape) // 80)
        sub = arr2[::step, ::step]
        vals = [[None if (not np.isfinite(v) or v == 0) else round(float(v), 3) for v in r] for r in sub]
        return vals, [w, s, e, n]

    return await asyncio.to_thread(_read_sync)


@router.get("/{field_id}/analysis/grid")
async def value_grid(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                     index: str = Query("NDVI")) -> dict[str, Any]:
    """Downsampled per-pixel value grid for map hover; clipped to the field."""
    await _own(db, field_id, current_user.id)
    vals, bounds = await _read_grid(db, field_id, index)
    if bounds is None:
        return {"bounds": None, "values": []}
    return {"bounds": bounds, "rows": len(vals), "cols": len(vals[0]) if vals else 0,
            "values": vals, "index": index.upper()}


@router.get("/{field_id}/anomaly-points")
async def anomaly_points(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                         index: str = Query("NDVI")) -> dict[str, Any]:
    """Attention pins: strongest below-expected spots in the index raster."""
    await _own(db, field_id, current_user.id)
    if index.upper() == "RIESGO":
        return {"points": []}
    vals, bounds = await _read_grid(db, field_id, index)
    if bounds is None:
        return {"points": []}
    from app.services.anomaly import raster_outlier_points
    return {"points": raster_outlier_points(vals, bounds), "index": index.upper()}


async def _current_weather(lat: float, lon: float) -> dict:
    """Fetch current temp/humidity and recent rain from Open-Meteo. {} on failure."""
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,precipitation",
                "daily": "precipitation_sum", "past_days": 2, "forecast_days": 1, "timezone": "auto"})
        if r.status_code != 200:
            return {}
        d = r.json()
        cur = d.get("current", {})
        rain = sum(d.get("daily", {}).get("precipitation_sum", []) or [0])
        return {"temp": cur.get("temperature_2m"), "humidity": cur.get("relative_humidity_2m"),
                "recent_rain_mm": rain}
    except Exception:
        return {}


@router.get("/{field_id}/pillars")
async def pillars(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """The three pillars for a field: Agua y Suelo, Plagas, Biomasa — real models."""
    row = (await db.execute(
        select(Field.crop_type, Field.planting_date, Field.context,
               ST_AsText(ST_Centroid(Field.geometry)).label("c"))
        .where(Field.id == field_id, Field.user_id == current_user.id))).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Campo no encontrado.")
    crop = row["crop_type"]

    async def latest(idx: str):
        return (await db.execute(
            select(Index.mean_value).where(Index.field_id == field_id, Index.index_type == idx,
                                           Index.mean_value.isnot(None))
            .order_by(Index.date.desc()).limit(1))).scalar_one_or_none()

    ndmi = await latest("NDMI")
    ndvi_rows = (await db.execute(
        select(Index.date, Index.mean_value).where(Index.field_id == field_id, Index.index_type == "NDVI",
                                                    Index.mean_value.isnot(None))
        .order_by(Index.date.asc()))).all()

    pt = shapely_wkt.loads(row["c"])
    lat = round(pt.y, 5)
    lon = round(((pt.x + 180) % 360) - 180, 5)
    wx = await _current_weather(lat, lon)

    # ── Pilar 1: Agua y Suelo (NDMI + contexto de riego) ──
    moisture_pct = None if ndmi is None else max(0, min(100, round((float(ndmi) + 0.2) / 0.8 * 100)))
    agua_status = "sin datos" if ndmi is None else "crítico" if ndmi < 0.0 else "atención" if ndmi < 0.2 else "ok"
    agua = {
        "ndmi": round(float(ndmi), 3) if ndmi is not None else None,
        "moisture_pct": moisture_pct, "status": agua_status,
        "riego": (row["context"] or {}).get("riego"),
        "recommendation": ("Sin datos satelitales aún." if ndmi is None else
            "Humedad foliar baja: revisar riego." if ndmi < 0.2 else "Humedad foliar adecuada."),
    }

    # ── Pilar 2: Plagas (modelo con clima real + NDMI) ──
    plagas = pest_risk_model(crop, wx.get("temp"), wx.get("humidity"),
                             wx.get("recent_rain_mm"), float(ndmi) if ndmi is not None else None)
    plagas["weather"] = wx

    # ── Pilar 3: Biomasa (integral NDVI + fenología) ──
    biomasa = estimate_biomass([(d, v) for d, v in ndvi_rows], crop, row["planting_date"])

    return {"field_id": str(field_id), "crop": crop,
            "agua_suelo": agua, "plagas": plagas, "biomasa": biomasa}


_FORECAST_DAYS = 4  # today + 3 days ahead — enough runway to say "the window opens in 2 days"


async def _weather_for_pests(lat: float, lon: float) -> dict:
    """Weather metrics for the pest model: temp, RH, leaf-wetness hours, daily means,
    plus a same-shape 3-day forecast (Open-Meteo's own model) for the trend view.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,precipitation",
                "hourly": "relative_humidity_2m",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean",
                "past_days": 30, "forecast_days": _FORECAST_DAYS, "timezone": "auto"})
        if r.status_code != 200:
            return {}
        d = r.json()
        cur = d.get("current", {})
        rh = d.get("hourly", {}).get("relative_humidity_2m", []) or []
        # "Today" boundary: past_days*24 hourly points precede it.
        today_start = 30 * 24
        wet_hours = sum(1 for v in rh[today_start - 72:today_start] if v is not None and v >= 90)
        daily = d.get("daily", {})
        tmax_all = daily.get("temperature_2m_max", []) or []
        tmin_all = daily.get("temperature_2m_min", []) or []
        rhmean_all = daily.get("relative_humidity_2m_mean", []) or []
        # Degree-days to date must stop at "today" — the forecast tail is handled
        # separately by forecast_pest() so future days aren't double-counted as past.
        upto_today = len(tmax_all) - _FORECAST_DAYS + 1
        means = [(a + b) / 2 for a, b in zip(tmax_all[:upto_today], tmin_all[:upto_today], strict=False)
                 if a is not None and b is not None]
        rain = sum((daily.get("precipitation_sum", []) or [0])[-(_FORECAST_DAYS + 2):-_FORECAST_DAYS] or [0])

        # Last _FORECAST_DAYS daily entries = today .. today+3.
        fc_tmax, fc_tmin, fc_rh = tmax_all[-_FORECAST_DAYS:], tmin_all[-_FORECAST_DAYS:], rhmean_all[-_FORECAST_DAYS:]
        forecast_days = []
        for i in range(len(fc_tmax)):
            t = (fc_tmax[i] + fc_tmin[i]) / 2 if fc_tmax[i] is not None and fc_tmin[i] is not None else None
            rh_d = fc_rh[i]
            # Hourly-derived wet_hours only exists for today; future days use a
            # coarse proxy from the daily mean humidity (documented, not precise).
            wh = wet_hours if i == 0 else (6 if (rh_d is not None and rh_d >= 90) else 3 if (rh_d is not None and rh_d >= 80) else 0)
            forecast_days.append({"offset": i, "temp": t, "humidity": rh_d, "wet_hours": wh})

        return {"temp": cur.get("temperature_2m"), "humidity": cur.get("relative_humidity_2m"),
                "wet_hours": wet_hours, "recent_rain_mm": rain, "daily_means": means,
                "forecast_days": forecast_days}
    except Exception:
        return {}


def _merged_catalog(ctx: dict | None) -> dict:
    cat = dict(PEST_CATALOG)
    for c in (ctx or {}).get("custom_pests", []) or []:
        if c.get("key"):
            cat[c["key"]] = c
    return cat


def _active_keys(ctx: dict | None, crop: str | None) -> list[str]:
    return (ctx or {}).get("pests") or default_pests_for_crop(crop)


@router.get("/{field_id}/pests")
async def pests(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Deep multi-pest risk for the field's ACTIVE pests (weather + phenology driven)."""
    row = (await db.execute(
        select(Field.crop_type, Field.planting_date, Field.context,
               ST_AsText(ST_Centroid(Field.geometry)).label("c"))
        .where(Field.id == field_id, Field.user_id == current_user.id))).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Campo no encontrado.")
    ctx = row["context"] or {}
    cat = _merged_catalog(ctx)
    keys = [k for k in _active_keys(ctx, row["crop_type"]) if k in cat]
    pt = shapely_wkt.loads(row["c"])
    lat = round(pt.y, 5)
    lon = round(((pt.x + 180) % 360) - 180, 5)
    wx = await _weather_for_pests(lat, lon)
    means = wx.get("daily_means", [])

    forecast_days = wx.get("forecast_days", [])
    results = []
    for key in keys:
        p = {**cat[key], "key": key}
        dd = None
        if p.get("kind") == "insect" and means:
            base = p.get("dd_base", 8)
            dd = sum(max(0.0, m - base) for m in means)
        one = assess_pests([p], wx.get("temp"), wx.get("humidity"),
                           wx.get("wet_hours"), dd)[0]
        one["key"] = key
        if forecast_days:
            one["trend"] = forecast_pest(p, forecast_days, dd)
        results.append(one)
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"field_id": str(field_id), "crop": row["crop_type"], "weather": wx, "pests": results}


@router.get("/{field_id}/pest-catalog")
async def pest_catalog(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Full catalog (predefined + custom) marking which pests are active for this field."""
    row = (await db.execute(
        select(Field.crop_type, Field.context).where(
            Field.id == field_id, Field.user_id == current_user.id))).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Campo no encontrado.")
    ctx = row["context"] or {}
    cat = _merged_catalog(ctx)
    active = set(_active_keys(ctx, row["crop_type"]))
    items = [{"key": k, "name": v["name"], "kind": v.get("kind"),
              "crops": v.get("crops", []), "scout": v.get("scout", ""),
              "active": k in active, "custom": k not in PEST_CATALOG} for k, v in cat.items()]
    return {"field_id": str(field_id), "crop": row["crop_type"], "catalog": items}


class PestCatalogUpdate(BaseModel):
    active: list[str] | None = None          # set the active pest keys
    add_custom: dict | None = None           # {key,name,kind,temp_opt,rh_min/wet_hours|dd_base/dd_threshold,crops,scout}


@router.patch("/{field_id}/pest-catalog")
async def update_pest_catalog(field_id: uuid.UUID, body: PestCatalogUpdate,
                              current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Activate/deactivate pests for this zone, or add a custom pest the farmer needs."""
    r = await db.execute(select(Field).where(Field.id == field_id, Field.user_id == current_user.id))
    field = r.scalar_one_or_none()
    if not field:
        raise HTTPException(404, "Campo no encontrado.")
    ctx = dict(field.context or {})
    if body.add_custom and body.add_custom.get("key"):
        customs = [c for c in ctx.get("custom_pests", []) if c.get("key") != body.add_custom["key"]]
        customs.append(body.add_custom)
        ctx["custom_pests"] = customs
        ctx.setdefault("pests", list(_active_keys(ctx, field.crop_type)))
        if body.add_custom["key"] not in ctx["pests"]:
            ctx["pests"].append(body.add_custom["key"])
    if body.active is not None:
        ctx["pests"] = body.active
    field.context = ctx
    await db.commit()
    return {"detail": "Catálogo actualizado.", "active": ctx.get("pests", [])}


@router.get("/{field_id}/phenology")
async def phenology(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Phenology/phenotype: stage, days since planting, expected vs observed NDVI, vigour."""
    row = (await db.execute(
        select(Field.crop_type, Field.planting_date).where(
            Field.id == field_id, Field.user_id == current_user.id))).mappings().one_or_none()
    if not row:
        raise HTTPException(404, "Campo no encontrado.")
    ndvi_rows = (await db.execute(
        select(Index.date, Index.mean_value).where(
            Index.field_id == field_id, Index.index_type == "NDVI", Index.mean_value.isnot(None))
        .order_by(Index.date.asc()))).all()
    return assess_phenology([(d, v) for d, v in ndvi_rows], row["crop_type"], row["planting_date"])


@router.post("/{field_id}/tasks/propose")
@limiter.limit(settings.AI_RATE_LIMIT)
async def propose_field_tasks(request: Request, field_id: uuid.UUID, current_user: AiUser, db: DBSession,
                              body: ProposeBody | None = None) -> dict[str, Any]:
    """Chat-driven: propose (not save) tasks from real data + optional conversation.

    Gated to plans that include AI (Free has no AI → HTTP 402) and rate-limited per IP.
    """
    r = await db.execute(select(Field).where(Field.id == field_id, Field.user_id == current_user.id))
    field = r.scalar_one_or_none()
    if not field:
        raise HTTPException(404, "Campo no encontrado.")
    return await propose_tasks(db, field, body.messages if body else None)


@router.post("/{field_id}/tasks")
async def create_one_task(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                          t: NewTask) -> dict[str, Any]:
    """Save a single task — from a chat proposal OR a click on the task map (lat/lon)."""
    await _own(db, field_id, current_user.id)
    tt = t.task_type if t.task_type in ("riego", "fertilizacion", "inspeccion", "otro") else "otro"
    task = FieldTask(field_id=field_id, task_type=tt, title=t.title[:255],
                     detail=(t.detail or "")[:500], priority=min(4, max(1, t.priority)),
                     recommended_value=(t.recommended_value or None), due_date=_date.today(),
                     lat=t.lat, lon=t.lon)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return {"id": str(task.id), "task_type": task.task_type, "title": task.title,
            "status": task.status, "lat": task.lat, "lon": task.lon}


@router.get("/{field_id}/analysis/export")
async def export_field(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
                       fmt: str = Query("csv", alias="format")) -> Response:
    """Export a field's full index observations (all sensors) as CSV or JSON.

    This is the per-field feed of the data pipeline (the same rows that would be
    bundled for cloud fusion). Only real observations — no synthetic noise.
    """
    await _own(db, field_id, current_user.id)
    from app.services.plans import plan_allows_export
    if not plan_allows_export(current_user.plan):
        raise HTTPException(402, "La exportación no está incluida en tu plan. Mejora tu plan para exportar.")
    rows = (await db.execute(
        select(Index.date, Index.index_type, Index.mean_value, Index.raster_uri, Index.extra_meta)
        .where(Index.field_id == field_id, Index.mean_value.isnot(None))
        .order_by(Index.date.asc()))).all()
    records = [{
        "field_id": str(field_id), "date": str(d), "index": it, "value": round(v, 5),
        "sensor": _sensor_of(m), "res_m": _res_of(m, _sensor_of(m)),
        "has_raster": bool(r),
    } for d, it, v, r, m in rows]

    if fmt == "json":
        return Response(content=json.dumps({"field_id": str(field_id), "n": len(records),
                                            "observations": records}, ensure_ascii=False),
                        media_type="application/json")
    # CSV
    header = "field_id,date,index,value,sensor,res_m,has_raster"
    lines = [header] + [f'{r["field_id"]},{r["date"]},{r["index"]},{r["value"]},{r["sensor"]},{r["res_m"]},{r["has_raster"]}' for r in records]
    return Response(content="\n".join(lines), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="field_{field_id}.csv"'})


@router.get("/{field_id}/analysis/datastats")
async def data_stats(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Per-sensor / per-index inventory for the Data panel (counts, date range, rasters)."""
    await _own(db, field_id, current_user.id)
    rows = (await db.execute(
        select(Index.index_type, Index.extra_meta, Index.date, Index.raster_uri)
        .where(Index.field_id == field_id, Index.mean_value.isnot(None)))).all()
    agg: dict[str, dict] = {}
    for it, m, d, r in rows:
        sk = _sensor_of(m)
        key = f"{sk}|{it}"
        a = agg.setdefault(key, {"sensor": sk, "index": it, "count": 0, "rasters": 0, "min": d, "max": d})
        a["count"] += 1
        if r:
            a["rasters"] += 1
        a["min"] = min(a["min"], d)
        a["max"] = max(a["max"], d)
    out = [{**a, "min": str(a["min"]), "max": str(a["max"])} for a in agg.values()]
    out.sort(key=lambda x: (x["sensor"], x["index"]))
    return {"field_id": str(field_id), "total_obs": len(rows), "breakdown": out}


@router.post("/{field_id}/analysis/fuse")
async def fuse(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession,
               index: str = Query("NDVI")) -> dict[str, Any]:
    """Harmonise sensors to the backbone and build a daily gap-filled (fused) series.

    Mean-level STARFM-lite: normalise each sensor to Sentinel-2, then fill days with
    no high-res observation using the coarse sensor's temporal change. Points are
    tagged 'observado' vs 'fusionado (estimado)'.
    """
    await _own(db, field_id, current_user.id)
    from app.services.plans import plan_allows_radar_fusion
    if not plan_allows_radar_fusion(current_user.plan):
        raise HTTPException(402, "La fusión multi-satélite no está incluida en tu plan. Mejora tu plan para usarla.")
    rows = (await db.execute(
        select(Index.date, Index.mean_value, Index.extra_meta).where(
            and_(Index.field_id == field_id, Index.index_type == index.upper(),
                 Index.mean_value.isnot(None))).order_by(Index.date.asc()))).all()
    if not rows:
        raise HTTPException(404, "Sin datos para fusionar.")

    by_sensor: dict[str, list[tuple]] = {}
    for d, v, meta in rows:
        by_sensor.setdefault(_sensor_of(meta), []).append((d, float(v)))

    backbone = by_sensor.get(BACKBONE_KEY, [])
    coefficients = {}
    normalized: dict[str, list[tuple]] = {}
    for sk, series in by_sensor.items():
        if sk == BACKBONE_KEY or not backbone:
            coefficients[sk] = {"gain": 1.0, "offset": 0.0}
            normalized[sk] = series
            continue
        src, bb = fusion.build_pairs(backbone, series, max_day_gap=8)
        gain, offset = fusion.normalize_gain_offset(src, bb)
        coefficients[sk] = {"gain": round(gain, 4), "offset": round(offset, 4)}
        normalized[sk] = [(d, fusion.apply_gain_offset(np.array([v]), gain, offset)[0]) for d, v in series]

    # Merge all normalized observations
    observed = []
    for sk, series in normalized.items():
        for d, v in series:
            observed.append({"date": d, "value": float(v), "sensor": sk, "kind": "observado"})
    observed.sort(key=lambda x: x["date"])

    # Gap-fill: for each coarse (MODIS) date with no nearby high-res observation,
    # estimate the high-res value by transferring the coarse temporal change from
    # the nearest high-res anchor (STARFM-lite at mean level).
    hi = sorted([(d, v) for sk in (BACKBONE_KEY, "ls") if sk in normalized for d, v in normalized[sk]])
    coarse = sorted(normalized.get("modis", []))
    fused_points = []
    if hi and coarse:
        for cd, cv in coarse:
            # skip if a high-res observation already exists within 8 days
            if any(abs((cd - hd).days) <= 8 for hd, _ in hi):
                continue
            anchor = min(hi, key=lambda h: abs((h[0] - cd).days))  # nearest high-res
            anchor_d, anchor_v = anchor
            base = min(coarse, key=lambda c: abs((c[0] - anchor_d).days))  # coarse near anchor
            est = anchor_v + (cv - base[1])
            est = max(-1.0, min(1.0, est))
            fused_points.append({"date": str(cd), "value": round(float(est), 4),
                                 "sensor": "fused", "kind": "fusionado (estimado)"})

    series_out = [{"date": str(p["date"]) if not isinstance(p["date"], str) else p["date"],
                   "value": round(p["value"], 4), "sensor": p["sensor"], "kind": p["kind"]}
                  for p in observed] + fused_points
    series_out.sort(key=lambda x: x["date"])

    # Persist coefficients for reuse / transparency
    coef_dir = Path(settings.DATA_DIR) / "fusion" / str(field_id)
    coef_dir.mkdir(parents=True, exist_ok=True)
    (coef_dir / f"{index.upper()}_coeffs.json").write_text(json.dumps(coefficients, indent=2))

    return {"index": index.upper(), "backbone": BACKBONE_KEY, "coefficients": coefficients,
            "n_observed": len(observed), "n_fused": len(fused_points), "series": series_out,
            "note": "Fusión diaria estimada (STARFM-lite a nivel de media). No es STARFM certificado."}
