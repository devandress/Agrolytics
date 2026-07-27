"""Unit tests for raster_outlier_points (map attention pins)."""

from app.services.anomaly import raster_outlier_points

_BOUNDS = [0.0, 0.0, 1.0, 1.0]  # w, s, e, n


def test_flags_low_patch():
    vals = [[0.6] * 10 for _ in range(10)]
    vals[2][3] = 0.05
    pts = raster_outlier_points(vals, _BOUNDS)
    assert len(pts) >= 1
    p = pts[0]
    assert p["value"] < 0.2 and p["delta"] < 0
    assert 0.0 <= p["lon"] <= 1.0 and 0.0 <= p["lat"] <= 1.0


def test_none_when_uniform():
    vals = [[0.5] * 10 for _ in range(10)]
    assert raster_outlier_points(vals, _BOUNDS) == []


def test_ignores_nodata():
    vals = [[None] * 10 for _ in range(10)]
    assert raster_outlier_points(vals, _BOUNDS) == []
