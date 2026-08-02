"""Sentinel-1 SAR (radar) ingestion service.

Optical Sentinel-2 ingestion (see ``satellite_ingestion.py``) is blocked whenever
clouds cover a field — which is why the optical pipeline had to relax its cloud
filter to 50% and look back 90 days. Sentinel-1 C-band radar penetrates cloud and
works day or night, so it provides a reliable, all-weather vegetation signal.

We pull the **Sentinel-1 RTC** (Radiometrically Terrain Corrected) collection from
Microsoft Planetary Computer (free, signed URLs, analysis-ready) and derive the
Radar Vegetation Index (RVI) and the VH/VV cross-pol ratio per field.

See ``docs/RADAR_SAR.md`` for the comparison of open-source SAR sources.
"""

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
from app.models.satellite_scene import SatelliteScene
from app.services import indices
from app.services.sensors import SENTINEL1

# Microsoft Planetary Computer — Sentinel-1 RTC (free, signed URLs via modifier)
_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
# La colección STAC y la CLAVE del sensor no son lo mismo. Guardar la colección
# ("sentinel-1-rtc") donde el resto del sistema espera la clave del registro
# ("s1") dejaba al radar sin nombre en la interfaz y fuera del aviso de próximo
# paso, porque REGISTRY.get() no lo encontraba.
_COLLECTION = SENTINEL1.collection
_LOOKBACK_DAYS = 90
# Sentinel-1 RTC backscatter assets (linear power).
_BAND_CANDIDATES = {
    "vv": ["vv", "VV"],
    "vh": ["vh", "VH"],
}


def _open_stac_client() -> Client:
    """Return a Planetary Computer STAC client with automatic URL signing."""
    import planetary_computer

    return Client.open(_STAC_URL, modifier=planetary_computer.sign_inplace)


def ingest_radar_for_fields(db: Session) -> int:
    """Search for new Sentinel-1 RTC scenes covering all fields and process them."""
    fields: list[Field] = db.query(Field).all()
    if not fields:
        logger.info("No fields found; skipping radar ingestion.")
        return 0

    client = _open_stac_client()
    date_range = _date_range()

    new_records = 0
    for field in fields:
        try:
            new_records += _process_field(db, client, field, date_range)
        except Exception as exc:
            logger.error(f"Radar: error processing field {field.id}: {exc}")

    return new_records


def ingest_radar_field_by_id(field_id: str) -> int:
    """Ingest radar for a single field by ID — usable from FastAPI BackgroundTasks."""
    import uuid

    from app.db.session import SyncSessionLocal

    with SyncSessionLocal() as db:
        field = db.query(Field).filter(Field.id == uuid.UUID(field_id)).first()
        if not field:
            logger.warning(f"Radar: field {field_id} not found.")
            return 0
        client = _open_stac_client()
        try:
            return _process_field(db, client, field, _date_range())
        except Exception as exc:
            logger.error(f"Radar ingestion failed for field {field_id}: {exc}")
            return 0


def _date_range() -> str:
    end_dt = datetime.utcnow()
    start_dt = end_dt - timedelta(days=_LOOKBACK_DAYS)
    return f"{start_dt.isoformat()}Z/{end_dt.isoformat()}Z"


def _process_field(db: Session, client: Client, field: Field, date_range: str) -> int:
    import json

    from geoalchemy2.functions import ST_AsGeoJSON
    from sqlalchemy import select

    geojson_str = db.execute(
        select(ST_AsGeoJSON(Field.geometry)).where(Field.id == field.id)
    ).scalar_one()
    geom_dict = json.loads(geojson_str)

    # GeoJSON requires closed rings; PostGIS may omit the closing point.
    if geom_dict.get("type") == "Polygon":
        for ring in geom_dict["coordinates"]:
            if len(ring) >= 3 and ring[0] != ring[-1]:
                ring.append(ring[0])

    search = client.search(
        collections=[_COLLECTION],
        intersects=shape(geom_dict),
        datetime=date_range,
        max_items=30,
    )

    new_records = 0
    for item in search.items():
        # Skip if this field already has a radar index for this scene.
        already_done = db.query(Index).filter(
            Index.field_id == field.id,
            Index.extra_meta["scene_id"].as_string() == item.id,
        ).first()
        if already_done:
            continue

        existing = db.query(SatelliteScene).filter_by(scene_id=item.id).first()

        try:
            acq_date = item.datetime.date() if item.datetime else date.today()

            if not existing:
                asset_href = ""
                for key in ("vv", "VV"):
                    if key in item.assets:
                        asset_href = item.assets[key].href
                        break
                scene = SatelliteScene(
                    scene_id=item.id,
                    satellite=_COLLECTION,
                    acquisition_date=acq_date,
                    cloud_cover=0.0,  # radar is cloud-independent
                    asset_url=asset_href,
                )
                db.add(scene)
                try:
                    db.flush()
                except Exception:
                    db.rollback()
                    scene = db.query(SatelliteScene).filter_by(scene_id=item.id).first()
                    if not scene:
                        raise
            else:
                scene = existing

            records = _compute_radar_indices(item, field, geom_dict, acq_date)
            for idx_record in records:
                idx_record.field_id = field.id
                db.add(idx_record)
                new_records += 1

            scene.processed_flag = True
            db.commit()
            logger.info(f"Radar: processed scene {item.id} for field {field.id} ({new_records} new)")

        except Exception as exc:
            db.rollback()
            logger.error(f"Radar: failed scene {item.id}: {exc}")

    return new_records


def _compute_radar_indices(item: Any, field: Field, geom_dict: dict, acq_date: date) -> list[Index]:
    out_dir = Path(settings.DATA_DIR) / "cog" / str(field.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    bands: dict[str, np.ndarray] = {}
    profile: dict = {}

    for band_name, candidates in _BAND_CANDIDATES.items():
        href = None
        for key in candidates:
            if key in item.assets:
                href = item.assets[key].href
                break
        if not href:
            logger.warning(f"Radar: no asset for band {band_name} in scene {item.id}")
            continue

        try:
            with rasterio.open(href) as src:
                from pyproj import Transformer

                transformer = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)
                if geom_dict["type"] == "Polygon":
                    reproj_coords = [
                        [list(transformer.transform(x, y)) for x, y in ring]
                        for ring in geom_dict["coordinates"]
                    ]
                    reprojected = shape({"type": "Polygon", "coordinates": reproj_coords})
                else:
                    reprojected = shape(geom_dict)

                clipped, transform = rasterio.mask.mask(src, [mapping(reprojected)], crop=True, nodata=0)
                profile = src.profile.copy()
                profile.update({
                    "driver": "GTiff", "height": clipped.shape[1], "width": clipped.shape[2],
                    "transform": transform, "tiled": True, "compress": "deflate",
                    "blockxsize": 256, "blockysize": 256,
                })
                bands[band_name] = clipped[0].astype(np.float32)
        except Exception as exc:
            logger.error(f"Radar: failed to read band {band_name} from {href}: {exc}")

    # Resample VH to VV grid if shapes differ.
    if "vv" in bands and "vh" in bands and bands["vv"].shape != bands["vh"].shape:
        try:
            from scipy.ndimage import zoom as ndimage_zoom

            ref = bands["vv"].shape
            zf = (ref[0] / bands["vh"].shape[0], ref[1] / bands["vh"].shape[1])
            bands["vh"] = ndimage_zoom(bands["vh"], zf, order=1).astype(np.float32)
        except Exception:
            del bands["vh"]

    records: list[Index] = []
    date_tag = acq_date.isoformat().replace("-", "")

    if "vv" in bands and "vh" in bands:
        valid_mask = (bands["vv"] > 0) & (bands["vh"] > 0)

        rvi = indices.rvi(bands["vv"], bands["vh"])
        valid = rvi[valid_mask]
        mean_rvi = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(rvi, out_dir / f"RVI_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="RVI", raster_uri=uri, mean_value=mean_rvi,
            extra_meta={"scene_id": item.id, "sensor": SENTINEL1.key},
        ))

        ratio = indices.vh_vv_ratio(bands["vv"], bands["vh"])
        valid = ratio[valid_mask]
        mean_ratio = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(ratio, out_dir / f"VHVV_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="VHVV", raster_uri=uri, mean_value=mean_ratio,
            extra_meta={"scene_id": item.id, "sensor": SENTINEL1.key},
        ))

    return records


def _save_cog(data: np.ndarray, path: Path, profile: dict) -> str:
    p = profile.copy()
    p.update({"count": 1, "dtype": "float32", "driver": "GTiff",
              "tiled": True, "compress": "deflate", "blockxsize": 256, "blockysize": 256})
    with rasterio.open(str(path), "w", **p) as dst:
        dst.write(data, 1)
    return str(path)
