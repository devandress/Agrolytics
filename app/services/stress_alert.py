"""Early-stress detection service.

Compares the current NDVI pixel distribution against the historical
25th-percentile baseline for the same Julian-week window.  If ≥15 % of the
valid area falls below the baseline, a stress alert is generated.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import rasterio
from loguru import logger
from sqlalchemy.orm import Session

from app.models.field import Field
from app.models.index import Index

# Alert threshold: fraction of area below p25 that triggers an alert
_STRESS_THRESHOLD = 0.15
# How many days either side of the acquisition DOY to include in the historical window
_DOY_WINDOW = 14
# Minimum number of historical rasters required for a reliable baseline
_MIN_HISTORICAL = 3


def detect_stress(field: Field, db: Session) -> dict[str, Any]:
    """Compute stress-alert payload for *field*.

    Steps:
      1. Load the most recent NDVI raster.
      2. Load all historical NDVI rasters within ±_DOY_WINDOW days-of-year.
      3. Compute the pixel-wise 25th-percentile baseline.
      4. Measure the fraction of area below baseline.
      5. Return a result dict (alert=True/False + details).

    Args:
        field:  ORM Field row.
        db:     Synchronous SQLAlchemy session.

    Returns:
        Dict with keys: alert (bool), stress_fraction, threshold, summary.

    Raises:
        ValueError: If no NDVI data is available.
    """
    # ── Fetch the latest NDVI index record ────────────────────────────────────
    latest: Index | None = (
        db.query(Index)
        .filter(
            Index.field_id == field.id,
            Index.index_type == "NDVI",
            Index.raster_uri.isnot(None),
        )
        .order_by(Index.date.desc())
        .first()
    )
    if not latest:
        raise ValueError(f"No NDVI data for field {field.id}.")

    current_doy = latest.date.timetuple().tm_yday

    # ── Load current raster ───────────────────────────────────────────────────
    current_ndvi = _load_raster(latest.raster_uri)
    valid_mask = (current_ndvi != 0) & ~np.isnan(current_ndvi)

    if valid_mask.sum() == 0:
        raise ValueError("Current NDVI raster has no valid pixels.")

    # ── Load historical rasters for the same Julian window ────────────────────
    historical_rows: list[Index] = (
        db.query(Index)
        .filter(
            Index.field_id == field.id,
            Index.index_type == "NDVI",
            Index.raster_uri.isnot(None),
            Index.date < latest.date,
        )
        .order_by(Index.date.asc())
        .all()
    )

    # Filter by day-of-year proximity
    window_rows = [
        r for r in historical_rows
        if abs(r.date.timetuple().tm_yday - current_doy) <= _DOY_WINDOW
    ]

    if len(window_rows) < _MIN_HISTORICAL:
        logger.warning(
            f"Only {len(window_rows)} historical rasters for field {field.id}; "
            f"using global p25 fallback (0.2)."
        )
        p25_baseline = np.full(current_ndvi.shape, 0.2, dtype=np.float32)
    else:
        p25_baseline = _compute_p25_baseline(window_rows, current_ndvi.shape)

    # ── Compute stress fraction ───────────────────────────────────────────────
    below_baseline = (current_ndvi < p25_baseline) & valid_mask
    stress_fraction = float(below_baseline.sum()) / float(valid_mask.sum())

    alert = stress_fraction >= _STRESS_THRESHOLD

    summary = (
        f"{stress_fraction * 100:.1f}% of the field is below the historical "
        f"p25 NDVI baseline ({_STRESS_THRESHOLD * 100:.0f}% threshold)."
    )

    logger.info(f"Stress check for field {field.id}: alert={alert}, fraction={stress_fraction:.3f}")

    return {
        "alert": alert,
        "stress_fraction": stress_fraction,
        "threshold": _STRESS_THRESHOLD,
        "current_mean_ndvi": float(np.nanmean(current_ndvi[valid_mask])),
        "p25_mean": float(np.nanmean(p25_baseline[valid_mask])),
        "acquisition_date": str(latest.date),
        "historical_rasters_used": len(window_rows),
        "summary": summary,
    }


def _load_raster(uri: str) -> np.ndarray:
    """Load the first band of a raster as float32."""
    with rasterio.open(uri) as src:
        return src.read(1).astype(np.float32)


def _compute_p25_baseline(rows: list[Index], target_shape: tuple[int, int]) -> np.ndarray:
    """Stack historical rasters and return the per-pixel 25th-percentile array.

    Rasters are resampled to *target_shape* if necessary.
    """
    arrays: list[np.ndarray] = []
    for row in rows:
        try:
            data = _load_raster(row.raster_uri)
            if data.shape != target_shape:
                from skimage.transform import resize as sk_resize
                data = sk_resize(data, target_shape, anti_aliasing=True).astype(np.float32)
            arrays.append(data)
        except Exception as exc:
            logger.warning(f"Could not load {row.raster_uri}: {exc}")

    if not arrays:
        return np.full(target_shape, 0.2, dtype=np.float32)

    stacked = np.stack(arrays, axis=0)
    return np.nanpercentile(stacked, 25, axis=0).astype(np.float32)
