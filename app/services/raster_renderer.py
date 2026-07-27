"""Raster rendering utilities.

Converts float32 GeoTIFF files to RGBA PNG overlays for Leaflet imageOverlay,
and generates synthetic rasters for demo fields (no real satellite data).
"""

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import transform_bounds


def render_to_png(
    raster_path: str,
    opacity: int = 255,   # fully solid pixels (no transparency)
    index_type: str = "NDVI",
    clip_geojson: dict | None = None,
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Read a float32 single-band raster, apply index-type-aware colormap, return (PNG bytes, WGS84 bounds).

    When *clip_geojson* (a WGS84 polygon) is given, pixels outside it are made fully
    transparent so nothing renders beyond the field perimeter.

    bounds tuple: (west, south, east, north)
    """
    with rasterio.open(raster_path) as src:
        data = src.read(1).astype(np.float32)
        src_crs, src_transform = src.crs, src.transform
        bounds_4326 = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
        w4326 = transform_from_bounds(*bounds_4326, data.shape[1], data.shape[0])

    rgba = _index_colormap(data, index_type, opacity)
    h, w = data.shape

    # Hard-clip to the field polygon: everything outside → alpha 0.
    if clip_geojson is not None:
        try:
            from rasterio.features import geometry_mask
            from rasterio.warp import transform_geom
            geom = transform_geom("EPSG:4326", src_crs, clip_geojson)
            outside = geometry_mask([geom], transform=src_transform, invert=False, out_shape=(h, w))
            rgba[outside, 3] = 0
        except Exception:
            pass

    with MemoryFile() as mem:
        with mem.open(
            driver="PNG", height=h, width=w, count=4, dtype="uint8",
            crs="EPSG:4326", transform=w4326,
        ) as dst:
            dst.write(rgba[:, :, 0], 1)
            dst.write(rgba[:, :, 1], 2)
            dst.write(rgba[:, :, 2], 3)
            dst.write(rgba[:, :, 3], 4)
        return mem.read(), bounds_4326  # (west, south, east, north)


# Fixed display ranges per index so layers from different sensors/dates are
# directly comparable on the same colour scale (key for the overlay/swipe viewer).
INDEX_SCALE: dict[str, tuple[float, float]] = {
    "NDVI": (0.0, 0.9),
    "NDMI": (-0.3, 0.6),
    "NDRE": (0.0, 0.6),
    "EVI": (0.0, 0.9),
    "RVI": (0.0, 1.5),
    "VHVV": (0.0, 1.0),
    "PESTRISK": (0.0, 100.0),
}


def _index_colormap(data: np.ndarray, index_type: str = "NDVI", opacity: int = 210) -> np.ndarray:
    """Dispatch to the right colormap based on index type, using fixed scales."""
    lo, hi = INDEX_SCALE.get(index_type, (0.0, 1.0))
    if index_type == "PESTRISK":
        return _risk_colormap(data, opacity, lo, hi)
    # All vegetation/moisture indices share the red→yellow→green ramp on their scale.
    return _ndvi_colormap(data, opacity, lo, hi)


def _risk_colormap(data: np.ndarray, opacity: int = 210, lo: float = 0.0, hi: float = 100.0) -> np.ndarray:
    """Map pest-risk 0–100 → RGBA. Green (low) → yellow (medium) → red (high)."""
    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(data) & (data != 0.0)
    v = np.clip((data - lo) / (hi - lo + 1e-9), 0.0, 1.0)
    # low→green(46,139,60), mid→amber(255,193,40), high→red(198,40,40)
    r = np.where(v < 0.5, 46 + (255 - 46) * (v / 0.5), 255 - (255 - 198) * ((v - 0.5) / 0.5))
    g = np.where(v < 0.5, 139 + (193 - 139) * (v / 0.5), 193 - (193 - 40) * ((v - 0.5) / 0.5))
    b = np.where(v < 0.5, 60 - (60 - 40) * (v / 0.5), 40)
    rgba[:, :, 0] = np.where(valid, np.clip(r, 0, 255), 0).astype(np.uint8)
    rgba[:, :, 1] = np.where(valid, np.clip(g, 0, 255), 0).astype(np.uint8)
    rgba[:, :, 2] = np.where(valid, np.clip(b, 0, 255), 0).astype(np.uint8)
    rgba[:, :, 3] = np.where(valid, opacity, 0)
    return rgba


def _moisture_colormap(data: np.ndarray, opacity: int = 210, lo: float = -0.3, hi: float = 0.6) -> np.ndarray:
    """Map NDMI/moisture float32 → RGBA. Brown=dry, blue=wet, on a fixed [lo,hi] scale."""
    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = (data != 0.0) & np.isfinite(data)
    v = np.clip((data - lo) / (hi - lo + 1e-9), 0.0, 1.0)
    r = np.clip(180 * (1.0 - v), 0, 180).astype(np.uint8)
    g = np.clip(120 * (1.0 - v * 0.7), 0, 120).astype(np.uint8)
    b = np.clip(60 + 195 * v, 60, 255).astype(np.uint8)
    rgba[:, :, 0] = np.where(valid, r, 0)
    rgba[:, :, 1] = np.where(valid, g, 0)
    rgba[:, :, 2] = np.where(valid, b, 0)
    rgba[:, :, 3] = np.where(valid, opacity, 0)
    return rgba


def _ndvi_colormap(data: np.ndarray, opacity: int = 210, lo: float = 0.0, hi: float = 0.9) -> np.ndarray:
    """Map a vegetation index → RGBA on a RdYlGn-like ramp over a fixed [lo,hi] scale."""
    h, w = data.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    valid = np.isfinite(data) & (data != 0.0)
    v = np.clip((data - lo) / (hi - lo + 1e-9), 0.0, 1.0)

    # Red high at low v, fading by mid-scale; green rising to mid-scale.
    r = np.clip(255 * (1.0 - v / 0.5), 0, 255).astype(np.uint8)
    g = np.clip(255 * v / 0.5, 0, 210).astype(np.uint8)
    b = np.full((h, w), 30, dtype=np.uint8)

    rgba[:, :, 0] = np.where(valid, r, 0)
    rgba[:, :, 1] = np.where(valid, g, 0)
    rgba[:, :, 2] = np.where(valid, b, 0)
    rgba[:, :, 3] = np.where(valid, opacity, 0)
    return rgba
