"""Satellite ingestion service.

Uses Microsoft Planetary Computer (free, Sentinel-2 L2A) instead of Element84
for more reliable signed URL access.  No API key required for read-only access.
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

# Microsoft Planetary Computer — Sentinel-2 L2A (free, signed URLs via modifier)
_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
_COLLECTION = "sentinel-2-l2a"
_MAX_CLOUD = 50.0   # raised from 20 → 50 to find more scenes
_LOOKBACK_DAYS = 365  # always pull the last year of data (today − 365 → today)


def _field_date_range(field: Field) -> str:
    """STAC datetime range: always the last 365 days (today − 365 → today)."""
    end = datetime.utcnow()
    start = end - timedelta(days=_LOOKBACK_DAYS)
    return f"{start.isoformat()}Z/{end.isoformat()}Z"


def _open_stac_client() -> Client:
    """Return a Planetary Computer STAC client with automatic URL signing."""
    try:
        import planetary_computer
        return Client.open(_STAC_URL, modifier=planetary_computer.sign_inplace)
    except Exception as exc:
        logger.warning(f"Planetary Computer client failed ({exc}), falling back to Element84")
        return Client.open("https://earth-search.aws.element84.com/v1")


def ingest_scenes_for_fields(db: Session) -> int:
    """Search for new Sentinel-2 scenes covering all active fields and process them."""
    fields: list[Field] = db.query(Field).all()
    if not fields:
        logger.info("No fields found; skipping ingestion.")
        return 0

    client = _open_stac_client()
    new_records = 0
    for field in fields:
        field_id = field.id
        try:
            new_records += _process_field(db, client, field, _field_date_range(field))
        except Exception as exc:
            db.rollback()
            logger.error(f"Error processing field {field_id}: {exc}")

    return new_records


def ingest_field_by_id(field_id: str) -> int:
    """Ingest a single field by ID — usable from FastAPI BackgroundTasks."""
    import uuid

    from app.db.session import SyncSessionLocal

    with SyncSessionLocal() as db:
        fid = uuid.UUID(field_id)
        field = db.query(Field).filter(Field.id == fid).first()
        if not field:
            logger.warning(f"Field {field_id} not found for ingestion.")
            return 0
        client = _open_stac_client()
        try:
            return _process_field(db, client, field, _field_date_range(field))
        except Exception as exc:
            logger.error(f"Ingestion failed for field {field_id}: {exc}")
            return 0


def _process_field(db: Session, client: Client, field: Field, date_range: str) -> int:
    import json

    from geoalchemy2.functions import ST_AsGeoJSON
    from sqlalchemy import select

    geojson_str = db.execute(
        select(ST_AsGeoJSON(Field.geometry)).where(Field.id == field.id)
    ).scalar_one()
    geom_dict = json.loads(geojson_str)

    # GeoJSON requires closed rings (first == last coord); PostGIS may omit the closing point.
    if geom_dict.get("type") == "Polygon":
        for ring in geom_dict["coordinates"]:
            if len(ring) >= 3 and ring[0] != ring[-1]:
                ring.append(ring[0])

    # Pass a Shapely geometry to avoid pystac_client's strict GeoJSON dict validation.
    search_geom = shape(geom_dict)

    search = client.search(
        collections=[_COLLECTION],
        intersects=search_geom,
        datetime=date_range,
        max_items=30,
        query={"eo:cloud_cover": {"lt": _MAX_CLOUD}},
    )

    new_records = 0
    for item in search.items():
        cloud = item.properties.get("eo:cloud_cover", 100.0)
        if cloud >= _MAX_CLOUD:
            continue

        # Skip only if THIS field already has an index record for this scene.
        # processed_flag is global (scene-level) so it cannot be used here —
        # the same scene must be re-processed for each new field.
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
                for key in ("red", "visual", "B04"):
                    if key in item.assets:
                        asset_href = item.assets[key].href
                        break
                scene = SatelliteScene(
                    scene_id=item.id,
                    satellite=_COLLECTION,
                    acquisition_date=acq_date,
                    cloud_cover=cloud,
                    asset_url=asset_href,
                )
                db.add(scene)
                try:
                    db.flush()
                except Exception:
                    # Another concurrent worker already inserted this scene; reload it.
                    db.rollback()
                    scene = db.query(SatelliteScene).filter_by(scene_id=item.id).first()
                    if not scene:
                        raise
            else:
                scene = existing

            indices = _compute_indices(item, field, geom_dict, acq_date)
            for idx_record in indices:
                idx_record.field_id = field.id
                db.add(idx_record)
                new_records += 1

            scene.processed_flag = True
            db.commit()
            logger.info(f"Processed scene {item.id} for field {field.id} ({new_records} new)")

        except Exception as exc:
            db.rollback()
            logger.error(f"Failed scene {item.id}: {exc}")

    return new_records


def _compute_indices(item: Any, field: Field, geom_dict: dict, acq_date: date) -> list[Index]:
    out_dir = Path(settings.DATA_DIR) / "cog" / str(field.id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Try both Planetary Computer and Element84 asset key naming conventions
    _BAND_CANDIDATES = {
        "red":     ["red", "B04", "B4"],
        "nir":     ["nir", "nir08", "B08", "B8"],
        "swir":    ["swir16", "swir-16", "B11"],
        "blue":    ["blue", "B02", "B2"],
        "rededge": ["rededge", "rededge1", "red-edge", "B05", "B5"],
    }

    bands: dict[str, np.ndarray] = {}
    band_profiles: dict[str, dict] = {}
    geom_shape = shape(geom_dict)

    for band_name, candidates in _BAND_CANDIDATES.items():
        href = None
        for key in candidates:
            if key in item.assets:
                href = item.assets[key].href
                break
        if not href:
            logger.warning(f"No asset found for band {band_name} in scene {item.id}")
            continue

        try:
            with rasterio.open(href) as src:
                from pyproj import Transformer
                transformer = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)

                if geom_dict["type"] == "Polygon":
                    rings = geom_dict["coordinates"]
                    reproj_coords = [
                        [list(transformer.transform(x, y)) for x, y in ring]
                        for ring in rings
                    ]
                    reprojected = shape({"type": "Polygon", "coordinates": reproj_coords})
                else:
                    reprojected = geom_shape

                clipped, transform = rasterio.mask.mask(src, [mapping(reprojected)], crop=True, nodata=0)
                band_profile = src.profile.copy()
                band_profile.update({
                    "driver": "GTiff", "height": clipped.shape[1], "width": clipped.shape[2],
                    "transform": transform, "tiled": True, "compress": "deflate",
                    "blockxsize": 256, "blockysize": 256,
                })
                band_profiles[band_name] = band_profile
                bands[band_name] = clipped[0].astype(np.float32)
        except Exception as exc:
            logger.error(f"Failed to read band {band_name} from {href}: {exc}")

    # Resample every band onto the finest common grid (native 10 m red/nir), and
    # use THAT band's own profile/transform for output — using whichever band's
    # profile happened to be read last would silently mismatch the resampled
    # array shape (rasterio doesn't validate this on write; it just writes the
    # array into a canvas sized from the stale profile, corrupting geolocation
    # and truncating resolution down to the coarsest band, e.g. 20 m SWIR/red-edge).
    ref_band = next(iter(bands), None)
    ref_shape = bands[ref_band].shape if ref_band else None
    profile = band_profiles[ref_band] if ref_band else {}
    if ref_shape:
        for k, arr in list(bands.items()):
            if arr.shape != ref_shape:
                try:
                    from scipy.ndimage import zoom as ndimage_zoom
                    zf = (ref_shape[0] / arr.shape[0], ref_shape[1] / arr.shape[1])
                    bands[k] = ndimage_zoom(arr, zf, order=1).astype(np.float32)
                except Exception:
                    del bands[k]

    records: list[Index] = []
    date_tag = acq_date.isoformat().replace("-", "")

    if "red" in bands and "nir" in bands:
        ndvi = indices.ndvi(bands["nir"], bands["red"])
        valid = ndvi[bands["red"] > 0]
        mean_ndvi = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(ndvi, out_dir / f"NDVI_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="NDVI", raster_uri=uri, mean_value=mean_ndvi,
            extra_meta={"scene_id": item.id, "cloud": item.properties.get("eo:cloud_cover")},
        ))

    if "nir" in bands and "swir" in bands:
        ndmi = indices.ndmi(bands["nir"], bands["swir"])
        valid = ndmi[bands["nir"] > 0]
        mean_ndmi = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(ndmi, out_dir / f"NDMI_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="NDMI", raster_uri=uri, mean_value=mean_ndmi,
            extra_meta={"scene_id": item.id},
        ))

    if "nir" in bands and "rededge" in bands:
        ndre = indices.ndre(bands["nir"], bands["rededge"])
        valid = ndre[bands["nir"] > 0]
        mean_ndre = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(ndre, out_dir / f"NDRE_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="NDRE", raster_uri=uri, mean_value=mean_ndre,
            extra_meta={"scene_id": item.id},
        ))

    if "red" in bands and "nir" in bands and "blue" in bands:
        evi = indices.evi(bands["nir"], bands["red"], bands["blue"])
        valid = evi[bands["red"] > 0]
        mean_evi = float(np.nanmean(valid)) if valid.size > 0 else None
        uri = _save_cog(evi, out_dir / f"EVI_{date_tag}.tif", profile)
        records.append(Index(
            date=acq_date, index_type="EVI", raster_uri=uri, mean_value=mean_evi,
            extra_meta={"scene_id": item.id},
        ))

    return records


def _save_cog(data: np.ndarray, path: Path, profile: dict) -> str:
    p = profile.copy()
    # height/width MUST match `data`'s own shape — rasterio does not validate this
    # on write, it silently writes into a canvas sized from the profile, corrupting
    # geolocation if a caller ever passes a profile from a different-resolution band.
    p.update({
        "count": 1, "dtype": "float32", "driver": "GTiff",
        "height": data.shape[0], "width": data.shape[1],
        "tiled": True, "compress": "deflate", "blockxsize": 256, "blockysize": 256,
    })
    with rasterio.open(str(path), "w", **p) as dst:
        dst.write(data, 1)
    return str(path)
