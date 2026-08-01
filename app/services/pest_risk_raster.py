"""Pest Risk Raster — per-pixel pest/disease pressure from index rasters + pest model.

Standard precision-ag method: operate mathematically on the field's index rasters
(NDVI = host vigour/microclimate, NDMI = canopy wetness) to derive a 0–100 **pest
risk raster**, then classify it into low/medium/high zones. Weather (temperature,
humidity) scales the whole field using the active pest's response window.

The per-pixel math is a pure NumPy function (`pixel_risk_array`) so it is unit-
testable without a database or GeoTIFF files.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from geoalchemy2.functions import ST_AsGeoJSON
from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.field import Field
from app.models.index import Index
from app.services.pest_catalog import PEST_CATALOG, default_pests_for_crop
from app.services.sensors import REGISTRY, normalize_sensor_key

RISK_SCALE = (0.0, 100.0)


def pixel_risk_array(
    ndvi: np.ndarray,
    ndmi: np.ndarray,
    pest: dict,
    temp_c: float | None = None,
    humidity_pct: float | None = None,
) -> np.ndarray:
    """Per-pixel pest risk 0–100 from NDVI + NDMI rasters and one pest's response.

    - Temperature & humidity are field-level scalars from the pest's window (broadcast).
    - NDMI (canopy wetness) and NDVI (host vigour/microclimate) drive the spatial pattern.
    - Fungal pests weight wetness heavily; insects weight host vigour/temperature.
    """
    kind = pest.get("kind", "fungal")
    lo, hi = pest.get("temp_opt", (12, 26))

    if temp_c is None:
        f_temp = 0.5
    elif lo <= temp_c <= hi:
        f_temp = 1.0
    else:
        f_temp = max(0.0, 1.0 - min(abs(temp_c - lo), abs(temp_c - hi)) / 6.0)

    if humidity_pct is None:
        f_rh = 0.5
    else:
        f_rh = min(1.0, max(0.0, (humidity_pct - (pest.get("rh_min", 85) - 15)) / 20.0))

    # Spatial drivers, normalised to 0..1.
    wet = np.clip((ndmi + 0.2) / 0.8, 0.0, 1.0)   # NDMI: wetter canopy → more fungal pressure
    host = np.clip(ndvi / 0.8, 0.0, 1.0)          # NDVI: denser canopy → more host / microclimate

    if kind == "fungal":
        risk = 100.0 * (0.30 * f_temp + 0.25 * f_rh + 0.30 * wet + 0.15 * host)
    else:  # insect
        risk = 100.0 * (0.40 * f_temp + 0.10 * f_rh + 0.15 * wet + 0.35 * host)
    return np.clip(risk, 0.0, 100.0).astype(np.float32)


def risk_hotspot_points(
    risk: np.ndarray,
    valid: np.ndarray,
    bounds: tuple[float, float, float, float],
    max_per_level: int = 2,
    min_sep_frac: float = 0.15,
) -> list[dict]:
    """Locate well-separated pest-risk hotspots as map pins.

    *bounds* is WGS84 ``(west, south, east, north)``. Mirrors the pixel→lat/lon
    math in ``anomaly.raster_outlier_points`` but flags HIGH risk instead of low
    values, in two severity bands: "alta" (>=70, red pin) and "media" (40-70,
    yellow/preventive pin). Returns up to *max_per_level* per band, each pin kept
    at least ``min_sep_frac`` of the field's extent away from the others already
    picked so nearby pixels of the same hotspot don't spawn a pin each.
    """
    w, s, e, n = bounds
    rows, cols = risk.shape
    sep = min_sep_frac * max(e - w, n - s)

    def _pick(lo: float, hi: float, level: str) -> list[dict]:
        cand: list[tuple[float, float, float]] = []
        for i in range(rows):
            for j in range(cols):
                if valid[i, j] and lo <= risk[i, j] < hi:
                    lon = w + (j + 0.5) / cols * (e - w)
                    lat = n - (i + 0.5) / rows * (n - s)
                    cand.append((float(risk[i, j]), lat, lon))
        cand.sort(key=lambda c: -c[0])  # worst (highest risk) first

        picked: list[tuple[float, float, float]] = []
        for val, lat, lon in cand:
            if all(abs(lat - p[1]) + abs(lon - p[2]) > sep for p in picked):
                picked.append((val, lat, lon))
            if len(picked) >= max_per_level:
                break
        return [{"lat": round(lat, 6), "lon": round(lon, 6), "risk": round(val, 1), "level": level}
                for val, lat, lon in picked]

    return _pick(70.0, 100.0001, "alta") + _pick(40.0, 70.0, "media")


async def build_pest_risk_pins(
    db: AsyncSession, field_id, crop_type: str | None, data_dir: str,
    temp_c: float | None = None, humidity_pct: float | None = None,
) -> list[dict]:
    """Build (or reuse) the field's pest-risk raster and return hotspot pins.

    Each pin is ``{lat, lon, risk, level, pest}`` — ``level`` "alta" (red,
    priority-1 inspection) or "media" (preventive). Empty when there's no
    NDVI raster yet for the field.
    """
    res = await build_pest_risk_raster(db, field_id, crop_type, data_dir, temp_c, humidity_pct)
    if not res:
        return []

    from rasterio.warp import transform_bounds

    with rasterio.open(res["path"]) as src:
        risk = src.read(1).astype(np.float32)
        bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds)

    valid = risk > 0.0
    points = risk_hotspot_points(risk, valid, bounds)
    for p in points:
        p["pest"] = res["pest"]
    return points


def zone_shares(risk: np.ndarray, valid: np.ndarray) -> dict:
    """Percentage of valid pixels in low (<40) / medium (40–70) / high (≥70) risk."""
    v = risk[valid]
    n = int(v.size)
    if n == 0:
        return {"low": 0, "medium": 0, "high": 0, "mean": None}
    return {
        "low": round(100 * float((v < 40).sum()) / n),
        "medium": round(100 * float(((v >= 40) & (v < 70)).sum()) / n),
        "high": round(100 * float((v >= 70).sum()) / n),
        "mean": round(float(v.mean()), 1),
    }


async def _field_geom(db: AsyncSession, field_id) -> dict | None:
    """Field polygon as GeoJSON (WGS84), for clipping the risk raster at build time."""
    g = (await db.execute(select(ST_AsGeoJSON(Field.geometry)).where(Field.id == field_id))).scalar_one_or_none()
    return json.loads(g) if g else None


def _primary_pest(crop_type: str | None) -> dict:
    """Pick the field's primary catalog pest (first default for the crop)."""
    keys = default_pests_for_crop(crop_type)
    return PEST_CATALOG[keys[0]] if keys else {"kind": "fungal", "temp_opt": (12, 26), "rh_min": 85}


def _res_of(meta: dict | None) -> int:
    """Native metres/pixel of the row's sensor, for choosing the sharpest raster."""
    if meta and meta.get("native_res_m"):
        return int(meta["native_res_m"])
    s = REGISTRY.get(normalize_sensor_key((meta or {}).get("sensor")))
    return s.native_res_m if s else 10


def pick_sharpest(
    rows: list[tuple[str, object, dict | None]], within_days: int = 20
) -> tuple[str, object] | None:
    """Sharpest recent raster from ``(uri, date, meta)`` rows, NOT simply the newest.

    Picking by date alone is how a 19 ha field ended up with a 3×4-pixel risk map:
    the most recent NDVI happened to be MODIS at 250 m/px, which covers that field
    in a dozen pixels — two of them actually inside the polygon. A pest map with two
    pixels cannot show a hotspot, so it renders as one flat blob.

    So: look at everything within *within_days* of the newest observation and take
    the finest resolution available, breaking ties by date. A 5-day-old Sentinel-2
    scene at 10 m says far more about where the pressure is than today's MODIS.

    Pure on purpose (same reasoning as ``pixel_risk_array``): the rule that decides
    which scene the whole pest map is built from deserves tests that don't need a
    database or GeoTIFFs on disk.
    """
    if not rows:
        return None
    newest = max(r[1] for r in rows)
    recent = [r for r in rows if (newest - r[1]).days <= within_days]
    best = min(recent, key=lambda r: (_res_of(r[2]), -r[1].toordinal()))
    return (best[0], best[1])


async def _best_raster(
    db: AsyncSession, field_id, index_type: str, within_days: int = 20
) -> tuple[str | None, object]:
    """DB wrapper around :func:`pick_sharpest` — fetches candidates, drops rows whose
    file is gone, and delegates the choice."""
    rows = (await db.execute(
        select(Index.raster_uri, Index.date, Index.extra_meta).where(
            and_(Index.field_id == field_id, Index.index_type == index_type,
                 Index.raster_uri.isnot(None))).order_by(Index.date.desc()).limit(40))).all()
    rows = [(r[0], r[1], r[2]) for r in rows if r[0] and Path(r[0]).exists()]
    best = pick_sharpest(rows, within_days)
    return best if best else (None, None)


async def _matching_raster(
    db: AsyncSession, field_id, index_type: str, near_date, within_days: int = 20
) -> str | None:
    """Raster of *index_type* closest in time to *near_date* (used to pair NDMI with
    the chosen NDVI). Falling back to a constant makes the wetness term — a third of
    the fungal score — identical in every pixel, which is half of why the old map
    was flat."""
    rows = (await db.execute(
        select(Index.raster_uri, Index.date).where(
            and_(Index.field_id == field_id, Index.index_type == index_type,
                 Index.raster_uri.isnot(None))).order_by(Index.date.desc()).limit(40))).all()
    cand = [r for r in rows if r[0] and Path(r[0]).exists()
            and abs((r[1] - near_date).days) <= within_days]
    if not cand:
        return None
    return min(cand, key=lambda r: abs((r[1] - near_date).days))[0]


async def build_pest_risk_raster(
    db: AsyncSession, field_id, crop_type: str | None, data_dir: str,
    temp_c: float | None = None, humidity_pct: float | None = None,
) -> dict | None:
    """Build (and cache) a pest-risk GeoTIFF for a field from its latest NDVI+NDMI.

    Returns ``{path, date, zones}`` or ``None`` when the field has no NDVI raster yet.
    """
    ndvi_uri, ndvi_date = await _best_raster(db, field_id, "NDVI")
    if not ndvi_uri or not Path(ndvi_uri).exists():
        return None
    ndmi_uri = await _matching_raster(db, field_id, "NDMI", ndvi_date)

    with rasterio.open(ndvi_uri) as src:
        ndvi = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        src_crs, src_transform = src.crs, src.transform

    if ndmi_uri and Path(ndmi_uri).exists():
        with rasterio.open(ndmi_uri) as src:
            ndmi = src.read(1).astype(np.float32)
        if ndmi.shape != ndvi.shape:
            from scipy.ndimage import zoom as ndimage_zoom
            zf = (ndvi.shape[0] / ndmi.shape[0], ndvi.shape[1] / ndmi.shape[1])
            ndmi = ndimage_zoom(ndmi, zf, order=1).astype(np.float32)
    else:
        ndmi = np.full_like(ndvi, 0.2)  # neutral wetness when NDMI missing

    pest = _primary_pest(crop_type)
    valid = (ndvi > 0.0) & np.isfinite(ndvi)
    # El ráster cubre el rectángulo que envuelve la parcela, así que sus bordes caen
    # sobre lotes vecinos que también tienen NDVI válido. Sin recortar acá, esos
    # píxeles entran en el reparto de zonas y —peor— pueden convertirse en un pin de
    # plaga clavado en el campo del vecino. El recorte al render llegaba tarde: sirve
    # para lo que se ve, no para lo que se calcula.
    geom = await _field_geom(db, field_id)
    if geom is not None:
        try:
            from rasterio.features import geometry_mask
            from rasterio.warp import transform_geom
            outside = geometry_mask(
                [transform_geom("EPSG:4326", src_crs, geom)],
                transform=src_transform, invert=False, out_shape=ndvi.shape,
            )
            valid &= ~outside
        except Exception as exc:  # una geometría rota no debe tumbar el mapa entero
            logger.warning(f"No se pudo recortar el ráster de riesgo al polígono: {exc}")
    risk = pixel_risk_array(ndvi, ndmi, pest, temp_c, humidity_pct)
    risk[~valid] = 0.0

    out_path = Path(data_dir) / "cog" / str(field_id) / f"PESTRISK_{str(ndvi_date).replace('-', '')}.tif"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile.update(count=1, dtype="float32", compress="deflate")
    with rasterio.open(str(out_path), "w", **profile) as dst:
        dst.write(risk, 1)

    logger.info(f"Pest-risk raster built for field {field_id} ({pest.get('name', '?')})")
    return {"path": str(out_path), "date": str(ndvi_date), "zones": zone_shares(risk, valid),
            "pest": pest.get("name", "plaga")}
