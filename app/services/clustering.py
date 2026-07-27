"""Management-zone clustering service.

Uses KMeans on NDVI pixel values to produce 3 management zones
(low / medium / high) and converts the raster clusters to GeoJSON polygons.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
from loguru import logger
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from sklearn.cluster import KMeans
from sqlalchemy.orm import Session

from app.models.field import Field
from app.models.index import Index

# Cluster labels ordered by mean NDVI ascending (0=low, 1=medium, 2=high)
_ZONE_LABELS = {0: "low", 1: "medium", 2: "high"}
_N_CLUSTERS = 3


def compute_zone_map(
    field: Field,
    db: Session,
    n_years: int = 2,
) -> dict[str, Any]:
    """Build a management-zone GeoJSON FeatureCollection for *field*.

    Loads all NDVI rasters within *n_years* years, stacks them, runs KMeans
    clustering, and vectorises the result.

    Args:
        field:   ORM Field object.
        db:      Synchronous SQLAlchemy session.
        n_years: Look-back window for NDVI history.

    Returns:
        A GeoJSON FeatureCollection with three features (one per zone).

    Raises:
        ValueError: If no NDVI rasters are available for the field.
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=365 * n_years)
    rows: list[Index] = (
        db.query(Index)
        .filter(
            Index.field_id == field.id,
            Index.index_type == "NDVI",
            Index.date >= cutoff,
            Index.raster_uri.isnot(None),
        )
        .order_by(Index.date.asc())
        .all()
    )

    if not rows:
        raise ValueError(f"No NDVI rasters found for field {field.id} in the past {n_years} years.")

    logger.info(f"Clustering {len(rows)} NDVI rasters for field {field.id}")

    # ── Load and stack rasters ────────────────────────────────────────────────
    arrays: list[np.ndarray] = []
    ref_transform = None
    ref_crs = None
    ref_shape: tuple[int, int] | None = None

    for row in rows:
        try:
            with rasterio.open(row.raster_uri) as src:
                data = src.read(1).astype(np.float32)
                if ref_shape is None:
                    ref_shape = data.shape
                    ref_transform = src.transform
                    ref_crs = src.crs
                else:
                    # Resize to common shape if needed
                    if data.shape != ref_shape:
                        from rasterio.enums import Resampling
                        from rasterio.warp import reproject
                        dst_data = np.empty(ref_shape, dtype=np.float32)
                        reproject(
                            source=data, destination=dst_data,
                            src_transform=src.transform, src_crs=src.crs,
                            dst_transform=ref_transform, dst_crs=ref_crs,
                            resampling=Resampling.bilinear,
                        )
                        data = dst_data
                arrays.append(data)
        except Exception as exc:
            logger.warning(f"Could not open raster {row.raster_uri}: {exc}")

    if not arrays:
        raise ValueError("All NDVI rasters failed to load.")

    stacked = np.stack(arrays, axis=0)  # (T, H, W)
    mean_ndvi = np.nanmean(stacked, axis=0)  # (H, W)

    # ── KMeans on valid pixels ────────────────────────────────────────────────
    valid_mask = ~np.isnan(mean_ndvi) & (mean_ndvi != 0)
    pixel_values = mean_ndvi[valid_mask].reshape(-1, 1)

    if len(pixel_values) < _N_CLUSTERS:
        raise ValueError("Too few valid pixels for clustering.")

    km = KMeans(n_clusters=_N_CLUSTERS, n_init=10, random_state=42)
    labels_flat = km.fit_predict(pixel_values)

    # Map cluster indices so label 0 = lowest mean NDVI
    cluster_means = km.cluster_centers_.flatten()
    order = np.argsort(cluster_means)
    remap = {old: new for new, old in enumerate(order)}
    labels_flat = np.array([remap[lbl] for lbl in labels_flat])

    label_raster = np.zeros(mean_ndvi.shape, dtype=np.int16)
    label_raster[valid_mask] = labels_flat + 1  # 0 = nodata

    # ── Vectorise clusters ────────────────────────────────────────────────────
    features: list[dict[str, Any]] = _raster_to_features(
        label_raster, ref_transform, ref_crs
    )

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "field_id": str(field.id),
            "n_rasters": len(arrays),
            "cluster_means": {
                _ZONE_LABELS[i]: float(cluster_means[order[i]]) for i in range(_N_CLUSTERS)
            },
        },
    }


def _raster_to_features(
    label_raster: np.ndarray,
    transform: Any,
    crs: Any,
) -> list[dict[str, Any]]:
    """Vectorise each integer zone label to a GeoJSON Feature polygon.

    Uses rasterio.features.shapes() and dissolves via Shapely.
    """
    import rasterio.features

    features = []
    for zone_idx, zone_name in _ZONE_LABELS.items():
        cluster_id = zone_idx + 1  # nodata = 0, cluster = 1/2/3
        mask = (label_raster == cluster_id).astype(np.uint8)
        shapes = list(rasterio.features.shapes(mask, transform=transform))
        polygons = [shape(geom) for geom, val in shapes if val == 1]
        if not polygons:
            continue
        dissolved = unary_union(polygons)
        features.append(
            {
                "type": "Feature",
                "geometry": mapping(dissolved),
                "properties": {
                    "zone": zone_name,
                    "cluster_id": cluster_id,
                    "crs": str(crs),
                },
            }
        )
    return features
