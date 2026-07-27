"""Yield estimation service.

Implements a simple linear model:  yield = a * NDVI_integrated + b

Default coefficients are provided per crop type.  If the user has historical
harvest data, calibrated coefficients can be passed in.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
from loguru import logger
from sqlalchemy.orm import Session

from app.models.field import Field
from app.models.index import Index

# Default linear model coefficients (a, b) per crop
# yield (t/ha) = a * NDVI_integral + b
# NDVI_integral is the temporal integral (sum) of mean NDVI values over the season.
_DEFAULT_COEFFICIENTS: dict[str, tuple[float, float]] = {
    "corn": (8.5, -1.2),
    "wheat": (5.2, -0.6),
    "soy": (4.0, -0.4),
    "rice": (6.1, -0.8),
    "sunflower": (3.8, -0.3),
    "default": (5.0, -0.5),
}

# Approximate growing season length in days (for integration window)
_SEASON_DAYS = 180


def estimate_yield(
    field: Field,
    db: Session,
    crop_type: str | None = None,
    coeff_a: float | None = None,
    coeff_b: float | None = None,
) -> dict[str, Any]:
    """Estimate yield for *field* based on integrated NDVI.

    Args:
        field:     ORM Field row.
        db:        Synchronous SQLAlchemy session.
        crop_type: Override crop from field record.
        coeff_a:   User-calibrated slope coefficient (kg/ha per NDVI unit).
        coeff_b:   User-calibrated intercept.

    Returns:
        Dict with yield estimate and diagnostic metadata.

    Raises:
        ValueError: If no NDVI records are available.
    """
    crop_key = (crop_type or field.crop_type or "default").lower()
    default_a, default_b = _DEFAULT_COEFFICIENTS.get(crop_key, _DEFAULT_COEFFICIENTS["default"])
    a = coeff_a if coeff_a is not None else default_a
    b = coeff_b if coeff_b is not None else default_b

    # Fetch NDVI time-series (mean values only — no raster I/O needed)
    cutoff = date.today()
    start = date(cutoff.year, 1, 1)  # from start of current year

    rows: list[Index] = (
        db.query(Index)
        .filter(
            Index.field_id == field.id,
            Index.index_type == "NDVI",
            Index.mean_value.isnot(None),
            Index.date >= start,
            Index.date <= cutoff,
        )
        .order_by(Index.date.asc())
        .all()
    )

    if not rows:
        raise ValueError(f"No NDVI time-series available for field {field.id}.")

    dates = np.array([(r.date - start).days for r in rows], dtype=np.float64)
    values = np.array([r.mean_value for r in rows], dtype=np.float64)

    # Trapezoidal integration over time
    ndvi_integral = float(np.trapz(values, dates)) / _SEASON_DAYS

    yield_t_ha = round(a * ndvi_integral + b, 2)
    # Clamp to non-negative
    yield_t_ha = max(0.0, yield_t_ha)

    logger.info(
        f"Yield estimate for field {field.id}: {yield_t_ha} t/ha "
        f"(NDVI_integral={ndvi_integral:.3f}, crop={crop_key})"
    )

    return {
        "yield_t_ha": yield_t_ha,
        "crop": crop_key,
        "ndvi_integral": round(ndvi_integral, 4),
        "n_observations": len(rows),
        "season_start": str(start),
        "season_end": str(cutoff),
        "model": {"a": a, "b": b},
        "calibrated": coeff_a is not None,
        "summary": f"Estimated yield: {yield_t_ha} t/ha ({crop_key.capitalize()})",
    }
