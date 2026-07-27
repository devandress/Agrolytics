"""Spectral and radar index formulas.

Pure functions that accept either scalars or numpy arrays, so the same code path
serves both raster math (in the ingestion services) and unit tests.

Optical bands follow Sentinel-2 naming:
    B02 blue · B03 green · B04 red · B05 red-edge · B07 red-edge · B08 NIR · B11 SWIR16

Radar (Sentinel-1) uses dual-polarisation backscatter (linear power):
    VV co-polarised · VH cross-polarised
"""

from __future__ import annotations

from typing import TypeVar, Union

import numpy as np

# Accept scalars or arrays interchangeably.
Numeric = TypeVar("Numeric", float, np.ndarray)
ArrayLike = Union[float, np.ndarray]


def _normalized_difference(a: ArrayLike, b: ArrayLike) -> ArrayLike:
    """Return ``(a - b) / (a + b)`` element-wise, yielding 0 where the sum is 0.

    Mirrors the divide-by-zero handling used throughout the raster pipeline so
    nodata pixels (which arrive as 0) collapse to 0 instead of NaN/inf.
    """
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    denom = a_arr + b_arr
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(denom != 0, (a_arr - b_arr) / denom, 0.0)
    result = np.clip(result, -1.0, 1.0)
    # Preserve scalar-in / scalar-out ergonomics for callers and tests.
    if np.isscalar(a) and np.isscalar(b):
        return float(result)
    return result.astype(np.float32)


def ndvi(nir: ArrayLike, red: ArrayLike) -> ArrayLike:
    """Normalised Difference Vegetation Index — greenness/biomass. Bands B08, B04."""
    return _normalized_difference(nir, red)


def ndmi(nir: ArrayLike, swir: ArrayLike) -> ArrayLike:
    """Normalised Difference Moisture Index — canopy water content. Bands B08, B11."""
    return _normalized_difference(nir, swir)


def ndre(nir: ArrayLike, red_edge: ArrayLike) -> ArrayLike:
    """Normalised Difference Red-Edge — chlorophyll/nitrogen. Bands B08 (or B07), B05."""
    return _normalized_difference(nir, red_edge)


def salinity_index(green: ArrayLike, red: ArrayLike) -> ArrayLike:
    """Soil Salinity Index ``sqrt(green * red)``. Bands B03, B04.

    Negative reflectance is clamped to 0 before the square root to avoid NaNs.
    """
    green_arr = np.asarray(green, dtype=np.float64)
    red_arr = np.asarray(red, dtype=np.float64)
    product = np.clip(green_arr * red_arr, 0.0, None)
    result = np.sqrt(product)
    if np.isscalar(green) and np.isscalar(red):
        return float(result)
    return result.astype(np.float32)


def evi(nir: ArrayLike, red: ArrayLike, blue: ArrayLike) -> ArrayLike:
    """Enhanced Vegetation Index — biomass with reduced atmosphere/soil noise.

    ``2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)``. Bands B08, B04, B02.
    """
    nir_arr = np.asarray(nir, dtype=np.float64)
    red_arr = np.asarray(red, dtype=np.float64)
    blue_arr = np.asarray(blue, dtype=np.float64)
    denom = nir_arr + 6.0 * red_arr - 7.5 * blue_arr + 1.0
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(denom != 0, 2.5 * (nir_arr - red_arr) / denom, 0.0)
    result = np.clip(result, -1.0, 1.0)
    if np.isscalar(nir) and np.isscalar(red) and np.isscalar(blue):
        return float(result)
    return result.astype(np.float32)


def rvi(vv: ArrayLike, vh: ArrayLike) -> ArrayLike:
    """Radar Vegetation Index ``4 * VH / (VV + VH)`` from Sentinel-1 backscatter.

    All-weather, cloud-independent proxy for vegetation density that complements
    optical NDVI. Inputs are linear-power backscatter (not dB). Result lies in
    roughly [0, 4]; bare/water surfaces tend toward 0 and dense canopy toward ~1+.
    """
    vv_arr = np.asarray(vv, dtype=np.float64)
    vh_arr = np.asarray(vh, dtype=np.float64)
    denom = vv_arr + vh_arr
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(denom != 0, 4.0 * vh_arr / denom, 0.0)
    result = np.clip(result, 0.0, 4.0)
    if np.isscalar(vv) and np.isscalar(vh):
        return float(result)
    return result.astype(np.float32)


def vh_vv_ratio(vv: ArrayLike, vh: ArrayLike) -> ArrayLike:
    """Cross-pol / co-pol ratio ``VH / VV`` — sensitive to canopy structure/biomass."""
    vv_arr = np.asarray(vv, dtype=np.float64)
    vh_arr = np.asarray(vh, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(vv_arr != 0, vh_arr / vv_arr, 0.0)
    if np.isscalar(vv) and np.isscalar(vh):
        return float(result)
    return result.astype(np.float32)
