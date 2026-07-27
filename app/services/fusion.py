"""Cross-sensor harmonisation and temporal fusion.

Three independent, unit-testable pieces:

1. ``resample_to_grid``      — put any raster on a common shape/grid (handles the
                               different native resolutions of each sensor).
2. ``normalize_gain_offset`` — derive a linear gain/offset that aligns one sensor's
                               index values onto the backbone sensor, from
                               near-coincident observation pairs.
3. ``fuse_gap``              — STARFM-lite: estimate a high-resolution image on a
                               day with no high-res observation by transferring the
                               coarse sensor's temporal change onto the last
                               high-res image.

These operate on plain numpy arrays so they can be tested without rasters or a DB.
The honest caveat: ``fuse_gap`` is an approximation, not certified STARFM, and a
fused product never truly exceeds the detail of its high-resolution source.
"""

from __future__ import annotations

import numpy as np


def resample_to_grid(data: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    """Resample *data* to *out_shape* with bilinear interpolation.

    Used to bring rasters of different native resolutions onto a common grid.
    Falls back to nearest-neighbour for tiny inputs. NaNs are treated as gaps.
    """
    if data.shape == out_shape:
        return data.astype(np.float32)
    try:
        from scipy.ndimage import zoom
        zf = (out_shape[0] / data.shape[0], out_shape[1] / data.shape[1])
        # order=1 bilinear; prefilter off to avoid ringing on index data.
        return zoom(np.nan_to_num(data), zf, order=1, prefilter=False).astype(np.float32)
    except Exception:
        # Nearest-neighbour fallback via index mapping.
        ys = (np.linspace(0, data.shape[0] - 1, out_shape[0])).astype(int)
        xs = (np.linspace(0, data.shape[1] - 1, out_shape[1])).astype(int)
        return data[np.ix_(ys, xs)].astype(np.float32)


def normalize_gain_offset(
    source_vals: np.ndarray, backbone_vals: np.ndarray
) -> tuple[float, float]:
    """Least-squares gain & offset mapping *source* index values onto *backbone*.

    Given paired near-coincident mean values (same field, close dates) returns
    ``(gain, offset)`` such that ``backbone ≈ gain * source + offset``. With fewer
    than two valid pairs it returns the identity ``(1.0, 0.0)``.
    """
    s = np.asarray(source_vals, dtype=np.float64)
    b = np.asarray(backbone_vals, dtype=np.float64)
    mask = np.isfinite(s) & np.isfinite(b)
    s, b = s[mask], b[mask]
    if s.size < 2 or np.ptp(s) < 1e-9:
        return 1.0, 0.0
    gain, offset = np.polyfit(s, b, 1)
    # Same physical index across sensors → gain must be positive and finite.
    # A non-positive slope means too few/noisy pairs; fall back to identity.
    if not np.isfinite(gain) or not np.isfinite(offset) or gain <= 0.05:
        return 1.0, 0.0
    return float(gain), float(offset)


def apply_gain_offset(data: np.ndarray, gain: float, offset: float) -> np.ndarray:
    """Apply ``gain * data + offset`` element-wise."""
    return (np.asarray(data, dtype=np.float32) * gain + offset).astype(np.float32)


def fuse_gap(
    highres_t0: np.ndarray,
    coarse_t0: np.ndarray,
    coarse_t1: np.ndarray,
    out_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    """STARFM-lite gap fill for a day with no high-resolution observation.

    Estimates the high-resolution image at t1 by adding the coarse sensor's
    temporal change (t0→t1), resampled to the high-resolution grid, onto the last
    high-resolution image::

        highres(t1) ≈ highres(t0) + resample(coarse(t1) - coarse(t0))

    All inputs are brought to *out_shape* (defaults to ``highres_t0`` shape).
    """
    out_shape = out_shape or highres_t0.shape
    hr0 = resample_to_grid(highres_t0, out_shape)
    delta = resample_to_grid(coarse_t1.astype(np.float32) - coarse_t0.astype(np.float32), out_shape)
    return (hr0 + delta).astype(np.float32)


def build_pairs(
    backbone_series: list[tuple],
    source_series: list[tuple],
    max_day_gap: int = 8,
) -> tuple[list[float], list[float]]:
    """Pair near-coincident observations between two (date, value) series.

    Returns ``(source_values, backbone_values)`` for dates within *max_day_gap*
    days of each other — the input to :func:`normalize_gain_offset`.
    """
    src_vals: list[float] = []
    bb_vals: list[float] = []
    for sd, sv in source_series:
        if sv is None:
            continue
        best = None
        for bd, bv in backbone_series:
            if bv is None:
                continue
            gap = abs((sd - bd).days)
            if gap <= max_day_gap and (best is None or gap < best[0]):
                best = (gap, bv)
        if best is not None:
            src_vals.append(float(sv))
            bb_vals.append(float(best[1]))
    return src_vals, bb_vals
