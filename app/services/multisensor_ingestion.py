"""Generic multi-sensor optical ingestion.

One code path serves every optical sensor in ``app.services.sensors`` (Sentinel-2,
Landsat, MODIS, …). For each scene it clips the needed bands to the field polygon,
computes the indices the sensor supports, writes a COG named
``{INDEX}_{sensorkey}_{YYYYMMDD}.tif`` and an ``Index`` row tagged with the sensor
in ``extra_meta`` (no schema change — the index_type enum already exists).

Reuses the reprojection/clip/COG conventions from ``satellite_ingestion``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import rasterio.mask
from loguru import logger
from pystac_client import Client
from shapely.geometry import mapping, shape
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.field import Field
from app.models.index import Index
from app.services import indices as idx
from app.services.sensors import (
    AUTO_OPTICAL,
    MIN_USEFUL_PIXELS,
    Sensor,
    find_asset,
    pixels_for_field,
    to_reflectance,
    useful_for_field,
)

_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
_LOOKBACK_DAYS = 120
_MAX_ITEMS = 20


def _client() -> Client:
    import planetary_computer
    return Client.open(_STAC_URL, modifier=planetary_computer.sign_inplace)


def ingest_all_sensors_for_field(field_id: str, lookback_days: int = _LOOKBACK_DAYS) -> dict[str, int]:
    """Ingest every auto-enabled optical sensor for one field. Returns counts per sensor."""
    import uuid

    from app.db.session import SyncSessionLocal

    out: dict[str, int] = {}
    with SyncSessionLocal() as db:
        field = db.query(Field).filter(Field.id == uuid.UUID(field_id)).first()
        if not field:
            logger.warning(f"Multisensor: field {field_id} not found.")
            return out
        client = _client()
        for sensor in AUTO_OPTICAL:
            try:
                out[sensor.key] = _ingest_sensor(db, client, field, sensor, lookback_days)
            except Exception as exc:
                logger.error(f"Multisensor {sensor.key} failed for {field_id}: {exc}")
                out[sensor.key] = 0
    return out


def ingest_sensor_for_all_fields(sensor: Sensor, db: Session, lookback_days: int = _LOOKBACK_DAYS) -> int:
    client = _client()
    total = 0
    for field in db.query(Field).all():
        try:
            total += _ingest_sensor(db, client, field, sensor, lookback_days)
        except Exception as exc:
            logger.error(f"Multisensor {sensor.key} field {field.id}: {exc}")
    return total


def _ingest_sensor(db: Session, client: Client, field: Field, sensor: Sensor, lookback_days: int) -> int:
    from geoalchemy2.functions import ST_AsGeoJSON
    from sqlalchemy import select

    # Un sensor demasiado grueso para el lote no se ingiere. MODIS cubre 19 ha en 3
    # píxeles: no muestra una zona seca ni un foco de plaga, y encima entra en la
    # serie con otra calibración, así que aporta ruido en vez de información. La
    # regla mira el tamaño del lote, no el nombre del sensor: en un rancho grande
    # MODIS pasa el umbral y se ingiere igual.
    if not useful_for_field(sensor, field.area_ha):
        logger.info(
            f"Multisensor: {sensor.key} omitido en {field.id} — "
            f"{pixels_for_field(sensor, field.area_ha or 0):.0f} px para "
            f"{field.area_ha or 0:.1f} ha (mínimo {MIN_USEFUL_PIXELS})"
        )
        return 0

    geojson_str = db.execute(select(ST_AsGeoJSON(Field.geometry)).where(Field.id == field.id)).scalar_one()
    geom = json.loads(geojson_str)
    if geom.get("type") == "Polygon":
        for ring in geom["coordinates"]:
            if len(ring) >= 3 and ring[0] != ring[-1]:
                ring.append(ring[0])

    end = datetime.utcnow()
    start = end - timedelta(days=lookback_days)
    search = client.search(
        collections=[sensor.collection],
        intersects=shape(geom),
        datetime=f"{start.isoformat()}Z/{end.isoformat()}Z",
        max_items=_MAX_ITEMS,
    )

    new_records = 0
    for item in search.items():
        acq = item.datetime.date() if item.datetime else date.today()
        # Skip if this field already has this sensor's index for this scene.
        exists = db.query(Index).filter(
            Index.field_id == field.id,
            Index.extra_meta["scene_id"].as_string() == item.id,
        ).first()
        if exists:
            continue
        try:
            records = _compute(item, field, geom, acq, sensor)
            for r in records:
                r.field_id = field.id
                db.add(r)
                new_records += 1
            if records:
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.error(f"{sensor.key}: scene {item.id} failed: {exc}")
    if new_records:
        logger.info(f"{sensor.key}: +{new_records} index records for field {field.id}")
    return new_records


def _clip(href: str, geom: dict) -> tuple[np.ndarray, dict] | None:
    """Reproject the field polygon to the raster CRS and clip the band."""
    from pyproj import Transformer
    with rasterio.open(href) as src:
        # Use the full CRS (WKT) — MODIS uses sinusoidal which has no EPSG code.
        tr = Transformer.from_crs("EPSG:4326", src.crs.to_wkt(), always_xy=True)
        if geom["type"] == "Polygon":
            coords = [[list(tr.transform(x, y)) for x, y in ring] for ring in geom["coordinates"]]
            shp = shape({"type": "Polygon", "coordinates": coords})
        else:
            shp = shape(geom)
        clipped, transform = rasterio.mask.mask(src, [mapping(shp)], crop=True, nodata=0)
        profile = src.profile.copy()
        profile.update({"driver": "GTiff", "height": clipped.shape[1], "width": clipped.shape[2],
                        "transform": transform, "count": 1, "dtype": "float32",
                        "tiled": True, "compress": "deflate", "blockxsize": 256, "blockysize": 256})
        return clipped[0].astype(np.float32), profile


def _compute(item: Any, field: Field, geom: dict, acq: date, sensor: Sensor) -> list[Index]:
    out_dir = Path(settings.DATA_DIR) / "cog" / str(field.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = acq.isoformat().replace("-", "")
    meta = {"scene_id": item.id, "sensor": sensor.key, "native_res_m": sensor.native_res_m}
    records: list[Index] = []

    def save(arr: np.ndarray, profile: dict, index_type: str) -> Index:
        valid = arr[np.isfinite(arr) & (arr != 0)]
        mean = float(np.nanmean(valid)) if valid.size else None
        path = out_dir / f"{index_type}_{sensor.key}_{tag}.tif"
        p = profile.copy()
        p.update({"count": 1, "dtype": "float32"})
        with rasterio.open(str(path), "w", **p) as dst:
            dst.write(arr, 1)
        return Index(date=acq, index_type=index_type, raster_uri=str(path),
                     mean_value=mean, extra_meta=dict(meta))

    # MODIS-style: precomputed NDVI/EVI index bands (scaled int16).
    if "ndvi" in sensor.bands or "evi" in sensor.bands:
        for it, bkey in (("NDVI", "ndvi"), ("EVI", "evi")):
            if bkey not in sensor.bands:
                continue
            href = find_asset(item.assets, sensor.bands[bkey])
            if not href:
                continue
            clip = _clip(href, geom)
            if clip is None:
                continue
            arr, profile = clip
            arr = to_reflectance(arr, sensor.scale, sensor.offset).astype(np.float32)
            arr = np.clip(arr, -1.0, 1.0)
            records.append(save(arr, profile, it))
        return records

    # Spectral sensors: read bands, apply scale/offset, compute indices.
    bands: dict[str, np.ndarray] = {}
    profile: dict = {}
    for bname, cands in sensor.bands.items():
        href = find_asset(item.assets, cands)
        if not href:
            continue
        clip = _clip(href, geom)
        if clip is None:
            continue
        arr, profile = clip
        # Conserva el 0 = sin dato. Con el desplazamiento de Landsat (−0.2) los
        # píxeles vacíos pasaban a valer −0.2 de reflectancia, y el remuestreo los
        # mezclaba con píxeles buenos en el borde de la parcela.
        bands[bname] = to_reflectance(arr, sensor.scale, sensor.offset).astype(np.float32)

    # Align all bands to a common shape (the red band reference).
    ref = bands.get("red")
    if ref is None or profile == {}:
        return records
    from app.services.fusion import resample_to_grid
    for k, a in list(bands.items()):
        if a.shape != ref.shape:
            bands[k] = resample_to_grid(a, ref.shape)

    if "NDVI" in sensor.indices and "nir" in bands and "red" in bands:
        records.append(save(idx.ndvi(bands["nir"], bands["red"]), profile, "NDVI"))
    if "NDMI" in sensor.indices and "nir" in bands and "swir" in bands:
        records.append(save(idx.ndmi(bands["nir"], bands["swir"]), profile, "NDMI"))
    if "NDRE" in sensor.indices and "nir" in bands and "rededge" in bands:
        records.append(save(idx.ndre(bands["nir"], bands["rededge"]), profile, "NDRE"))
    if "EVI" in sensor.indices and all(b in bands for b in ("nir", "red", "blue")):
        records.append(save(idx.evi(bands["nir"], bands["red"], bands["blue"]), profile, "EVI"))
    return records
