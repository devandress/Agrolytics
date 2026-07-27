"""Anomaly detection for per-field spectral index time-series.

Two complementary signals:

1. **Statistical** — a z-score of the latest reading against the field's own
   historical baseline (mean/std per index), combined with a multi-week downward
   trend, classified into ``ok`` / ``warning`` / ``alert``.
2. **Agronomic** — absolute thresholds per crop type (the values supplied for
   Lechuga, Espinaca, Brócoli, …). These catch values that are dangerous in
   absolute terms even when the field has no anomalous *change*.

All functions are pure and operate on plain numbers / lists of ``(date, value)``
tuples, so they are unit-testable without a database or rasters.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np

# ── Statistical classification thresholds ──────────────────────────────────────
_Z_ALERT = -2.5      # z below this (or a sustained downtrend) → alert
_Z_WARNING = -1.5    # z below this → warning
_TREND_MIN_WEEKS = 3  # consecutive declining readings that count as a downtrend

# ── Per-crop agronomic thresholds (as provided) ────────────────────────────────
# warn = first concern, crit = critical. NDMI/NDRE are "low is bad" (moisture /
# chlorophyll); salinity_crit is "high is bad" (soil salinity index).
CROP_THRESHOLDS: dict[str, dict[str, float]] = {
    "Lechuga":   {"ndmi_warn": 0.45, "ndmi_crit": 0.30, "ndre_warn": 0.42, "ndre_crit": 0.28, "salinity_crit": 3.0},
    "Espinaca":  {"ndmi_warn": 0.42, "ndmi_crit": 0.28, "ndre_warn": 0.40, "ndre_crit": 0.25, "salinity_crit": 3.0},
    "Brócoli":   {"ndmi_warn": 0.40, "ndmi_crit": 0.25, "ndre_warn": 0.38, "ndre_crit": 0.22, "salinity_crit": 4.0},
    "Coliflor":  {"ndmi_warn": 0.40, "ndmi_crit": 0.25, "ndre_warn": 0.38, "ndre_crit": 0.22, "salinity_crit": 3.5},
    "Fresa":     {"ndmi_warn": 0.50, "ndmi_crit": 0.35, "ndre_warn": 0.45, "ndre_crit": 0.30, "salinity_crit": 2.5},
    "Apio":      {"ndmi_warn": 0.48, "ndmi_crit": 0.33, "ndre_warn": 0.42, "ndre_crit": 0.28, "salinity_crit": 3.0},
    "Alcachofa": {"ndmi_warn": 0.35, "ndmi_crit": 0.20, "ndre_warn": 0.35, "ndre_crit": 0.20, "salinity_crit": 4.0},
    # Wine grapes tolerate (and under RDI, target) low canopy moisture — only severe
    # NDMI alarms. NDRE/vigour runs lower than leafy veg. Moderately salt-sensitive.
    "Vid":       {"ndmi_warn": 0.20, "ndmi_crit": 0.10, "ndre_warn": 0.30, "ndre_crit": 0.20, "salinity_crit": 3.5},
}

# Fallback when the field's crop_type is unknown / unmapped — uses the mildest
# (most permissive) of the agronomic limits so we avoid false alarms.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "ndmi_warn": 0.40, "ndmi_crit": 0.25,
    "ndre_warn": 0.38, "ndre_crit": 0.22,
    "salinity_crit": 4.0,
}


def thresholds_for_crop(crop_type: str | None) -> dict[str, float]:
    """Return the agronomic threshold set for *crop_type* (case-insensitive).

    Falls back to :data:`DEFAULT_THRESHOLDS` for unknown or missing crops.
    """
    if not crop_type:
        return dict(DEFAULT_THRESHOLDS)
    # Case-insensitive match against the Spanish crop names.
    for name, limits in CROP_THRESHOLDS.items():
        if name.lower() == crop_type.strip().lower():
            return dict(limits)
    return dict(DEFAULT_THRESHOLDS)


def compute_baseline(readings: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Compute per-index mean and (population) std from historical *readings*.

    Args:
        readings: iterable of dicts with ``index_type`` and ``value`` keys
            (e.g. rows projected from the ``indices`` table). ``None`` values are
            skipped.

    Returns:
        ``{index_type: {"mean": float, "std": float, "n": int}}``.
    """
    grouped: dict[str, list[float]] = {}
    for r in readings:
        value = r.get("value")
        idx = r.get("index_type")
        if value is None or idx is None:
            continue
        grouped.setdefault(idx, []).append(float(value))

    baseline: dict[str, dict[str, float]] = {}
    for idx, values in grouped.items():
        n = len(values)
        mean = sum(values) / n
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / n
            std = math.sqrt(variance)
        else:
            std = 0.0
        baseline[idx] = {"mean": mean, "std": std, "n": n}
    return baseline


def anomaly_score(value: float, mean: float, std: float) -> float:
    """Return the z-score ``(value - mean) / std``; 0 when std is 0 (no spread)."""
    if std == 0:
        return 0.0
    return (value - mean) / std


def detect_trend(series: Sequence[float]) -> bool:
    """Return True if the series declines for ``_TREND_MIN_WEEKS`` steps in a row.

    *series* must be ordered oldest→newest. A "decline" is any strictly decreasing
    step; we look for the longest run of consecutive declines and compare its
    length (in steps) against the configured minimum.
    """
    cleaned = [float(v) for v in series if v is not None]
    if len(cleaned) <= _TREND_MIN_WEEKS:
        # Need at least _TREND_MIN_WEEKS decreasing *steps*, i.e. that many + 1 points.
        if len(cleaned) < _TREND_MIN_WEEKS + 1:
            return False

    consecutive = 0
    for prev, curr in zip(cleaned, cleaned[1:], strict=False):
        if curr < prev:
            consecutive += 1
            if consecutive >= _TREND_MIN_WEEKS:
                return True
        else:
            consecutive = 0
    return False


def classify(z: float, trend: bool) -> str:
    """Map a z-score and downtrend flag to ``"alert"`` / ``"warning"`` / ``"ok"``.

    - ``z < -2.5`` **or** a sustained downtrend → ``alert``
    - ``z < -1.5`` → ``warning``
    - otherwise → ``ok``
    """
    if z < _Z_ALERT or trend:
        return "alert"
    if z < _Z_WARNING:
        return "warning"
    return "ok"


def evaluate_field_thresholds(
    crop_type: str | None,
    latest_values: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Check the latest index values against per-crop agronomic thresholds.

    Args:
        crop_type: the field's crop (Spanish name) or None.
        latest_values: ``{index_type: value}`` — e.g. ``{"NDMI": 0.31, "NDRE": 0.4}``.
            Index names are matched case-insensitively; a ``SALINITY`` value is
            compared against ``salinity_crit`` (high is bad).

    Returns:
        ``{index_type: {"value", "status", "threshold"}}`` where status is one of
        ``ok`` / ``warning`` / ``critical``.
    """
    limits = thresholds_for_crop(crop_type)
    out: dict[str, dict[str, Any]] = {}

    for raw_idx, value in latest_values.items():
        if value is None:
            continue
        idx = raw_idx.upper()
        if idx in ("NDMI", "NDRE"):
            warn = limits[f"{idx.lower()}_warn"]
            crit = limits[f"{idx.lower()}_crit"]
            if value <= crit:
                status, ref = "critical", crit
            elif value <= warn:
                status, ref = "warning", warn
            else:
                status, ref = "ok", warn
            out[idx] = {"value": value, "status": status, "threshold": ref}
        elif idx in ("SALINITY", "SALINITY_INDEX"):
            crit = limits["salinity_crit"]
            status = "critical" if value >= crit else "ok"
            out["SALINITY"] = {"value": value, "status": status, "threshold": crit}

    return out


def raster_outlier_points(
    values: Sequence[Sequence[float | None]],
    bounds: Sequence[float],
    max_points: int = 6,
    min_sep_frac: float = 0.12,
) -> list[dict[str, Any]]:
    """Locate the strongest low-value spatial outliers in an index grid.

    *values* is a 2D grid (rows top→bottom, ``None``/0 = no data), *bounds* is
    ``[west, south, east, north]`` (WGS84). Flags pixels far BELOW the field mean
    (``mean − max(0.15, 1.5·std)``) — a change outside the expected pattern — and
    returns up to *max_points* well-separated worst spots as ``{lat, lon, value, delta}``.
    Used to drop attention pins on the map.
    """
    arr = np.array([[np.nan if v is None else float(v) for v in row] for row in values], dtype=float)
    if arr.size == 0:
        return []
    valid = np.isfinite(arr) & (arr != 0.0)
    v = arr[valid]
    if v.size < 10:
        return []
    mean, std = float(v.mean()), float(v.std())
    thr = mean - max(0.15, 1.5 * std)
    w, s, e, n = bounds
    rows, cols = arr.shape

    cand: list[tuple[float, float, float]] = []
    for i in range(rows):
        for j in range(cols):
            x = arr[i, j]
            if np.isfinite(x) and x != 0.0 and x < thr:
                lon = w + (j + 0.5) / cols * (e - w)
                lat = n - (i + 0.5) / rows * (n - s)
                cand.append((x, lat, lon))
    cand.sort(key=lambda c: c[0])  # worst (lowest) first

    picked: list[tuple[float, float, float]] = []
    sep = min_sep_frac * max(e - w, n - s)
    for x, lat, lon in cand:
        if all(abs(lat - p[1]) + abs(lon - p[2]) > sep for p in picked):
            picked.append((x, lat, lon))
        if len(picked) >= max_points:
            break
    return [{"lat": round(lat, 6), "lon": round(lon, 6), "value": round(x, 3),
             "delta": round(x - mean, 3)} for x, lat, lon in picked]


def analyze_index(
    history: Sequence[tuple[date, float]],
) -> dict[str, Any]:
    """Run the full statistical pipeline for a single index time-series.

    *history* is a list of ``(date, value)`` ordered oldest→newest, including the
    most recent reading as the last element.

    Returns a dict with ``status``, ``z_score``, ``trend``, ``mean``, ``std`` and
    ``latest``. Returns status ``"insufficient_data"`` when there is no history.
    """
    points = [(d, float(v)) for d, v in history if v is not None]
    if not points:
        return {"status": "insufficient_data", "z_score": None, "trend": False}

    values = [v for _, v in points]
    latest = values[-1]
    prior = values[:-1] or values  # baseline excludes the latest point when possible

    mean = sum(prior) / len(prior)
    if len(prior) > 1:
        std = math.sqrt(sum((v - mean) ** 2 for v in prior) / len(prior))
    else:
        std = 0.0

    z = anomaly_score(latest, mean, std)
    trend = detect_trend(values)
    return {
        "status": classify(z, trend),
        "z_score": z,
        "trend": trend,
        "mean": mean,
        "std": std,
        "latest": latest,
        "n": len(points),
    }
