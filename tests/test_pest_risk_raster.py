"""Unit tests for the per-pixel pest-risk math (no DB/GeoTIFF needed)."""

import numpy as np

from app.services.pest_risk_raster import pixel_risk_array, risk_hotspot_points, zone_shares

_FUNGAL = {"kind": "fungal", "temp_opt": (15, 25), "rh_min": 85}
_INSECT = {"kind": "insect", "temp_opt": (20, 30), "rh_min": 60}


def test_risk_in_range():
    ndvi = np.array([[0.2, 0.6, 0.8]], dtype="float32")
    ndmi = np.array([[0.0, 0.3, 0.5]], dtype="float32")
    r = pixel_risk_array(ndvi, ndmi, _FUNGAL, temp_c=20, humidity_pct=90)
    assert r.shape == ndvi.shape
    assert float(r.min()) >= 0 and float(r.max()) <= 100


def test_fungal_rises_with_wetness():
    ndvi = np.array([[0.6]], dtype="float32")
    dry = pixel_risk_array(ndvi, np.array([[0.0]], "float32"), _FUNGAL, 20, 90)
    wet = pixel_risk_array(ndvi, np.array([[0.5]], "float32"), _FUNGAL, 20, 90)
    assert wet[0, 0] > dry[0, 0]


def test_insect_rises_with_host_vigour():
    ndmi = np.array([[0.2]], dtype="float32")
    low = pixel_risk_array(np.array([[0.2]], "float32"), ndmi, _INSECT, 25, 60)
    high = pixel_risk_array(np.array([[0.8]], "float32"), ndmi, _INSECT, 25, 60)
    assert high[0, 0] > low[0, 0]


def test_temp_out_of_window_lowers_risk():
    ndvi = np.array([[0.6]], dtype="float32")
    ndmi = np.array([[0.4]], dtype="float32")
    inwin = pixel_risk_array(ndvi, ndmi, _FUNGAL, temp_c=20, humidity_pct=90)
    cold = pixel_risk_array(ndvi, ndmi, _FUNGAL, temp_c=0, humidity_pct=90)
    assert inwin[0, 0] > cold[0, 0]


def test_zone_shares_sum_100():
    risk = np.array([[10, 50, 80, 95]], dtype="float32")
    valid = np.ones_like(risk, dtype=bool)
    z = zone_shares(risk, valid)
    assert z["low"] + z["medium"] + z["high"] == 100
    assert z["mean"] is not None


def test_hotspots_classify_by_severity():
    # Low-risk corner, one medium hotspot, one high hotspot, far apart.
    risk = np.full((10, 10), 10.0, dtype="float32")
    risk[1, 1] = 55.0    # medium
    risk[8, 8] = 90.0    # high
    valid = np.ones_like(risk, dtype=bool)
    bounds = (-100.0, 0.0, -99.0, 1.0)  # 1deg x 1deg square, west/south/east/north
    pins = risk_hotspot_points(risk, valid, bounds)
    levels = {p["level"] for p in pins}
    assert levels == {"alta", "media"}
    high = next(p for p in pins if p["level"] == "alta")
    assert high["risk"] == 90.0


def test_hotspots_empty_when_all_low_risk():
    risk = np.full((5, 5), 15.0, dtype="float32")
    valid = np.ones_like(risk, dtype=bool)
    bounds = (-100.0, 0.0, -99.0, 1.0)
    assert risk_hotspot_points(risk, valid, bounds) == []


def test_hotspots_respect_min_separation():
    # Two adjacent high-risk pixels should collapse into a single pin.
    risk = np.full((10, 10), 5.0, dtype="float32")
    risk[5, 5] = 95.0
    risk[5, 6] = 92.0
    valid = np.ones_like(risk, dtype=bool)
    bounds = (-100.0, 0.0, -99.0, 1.0)
    pins = [p for p in risk_hotspot_points(risk, valid, bounds) if p["level"] == "alta"]
    assert len(pins) == 1
    assert pins[0]["risk"] == 95.0
