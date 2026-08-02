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


# ── Elección de la escena sobre la que se construye TODO el mapa de riesgo ──
# El bug real: un lote de 19 ha quedó con un ráster de riesgo de 3×4 píxeles (2 dentro
# del polígono) porque la observación más reciente era MODIS a 250 m. Elegir por fecha
# sin mirar resolución convierte el mapa en una mancha de un solo color.

from datetime import date  # noqa: E402

from app.services.pest_risk_raster import pick_sharpest  # noqa: E402

_S2 = {"sensor": "s2", "native_res_m": 10}
_LS = {"sensor": "ls", "native_res_m": 30}
_MODIS = {"sensor": "modis", "native_res_m": 250}


def test_no_rows_gives_nothing():
    assert pick_sharpest([]) is None


def test_prefers_sharper_over_newer():
    rows = [
        ("modis.tif", date(2026, 7, 28), _MODIS),   # más nueva pero 250 m
        ("s2.tif", date(2026, 7, 23), _S2),         # 5 días más vieja, 10 m
    ]
    assert pick_sharpest(rows)[0] == "s2.tif"


def test_ties_on_resolution_break_by_date():
    rows = [
        ("s2_vieja.tif", date(2026, 7, 10), _S2),
        ("s2_nueva.tif", date(2026, 7, 23), _S2),
    ]
    assert pick_sharpest(rows)[0] == "s2_nueva.tif"


def test_stale_sharp_scene_is_not_used_forever():
    """Una escena nítida de hace meses ya no describe el cultivo de hoy: fuera de la
    ventana no compite, aunque sea la de mejor resolución."""
    rows = [
        ("s2_marzo.tif", date(2026, 3, 1), _S2),
        ("ls_julio.tif", date(2026, 7, 28), _LS),
    ]
    assert pick_sharpest(rows, within_days=20)[0] == "ls_julio.tif"


def test_window_is_measured_from_the_newest_row_not_from_today():
    """Si el campo lleva un mes sin imágenes, igual hay que elegir entre las que hay
    en vez de quedarse sin mapa."""
    rows = [
        ("modis.tif", date(2026, 1, 20), _MODIS),
        ("s2.tif", date(2026, 1, 15), _S2),
    ]
    assert pick_sharpest(rows, within_days=20)[0] == "s2.tif"


def test_row_order_does_not_change_the_choice():
    rows = [
        ("s2.tif", date(2026, 7, 23), _S2),
        ("modis.tif", date(2026, 7, 28), _MODIS),
    ]
    assert pick_sharpest(rows)[0] == "s2.tif"
    assert pick_sharpest(list(reversed(rows)))[0] == "s2.tif"


def test_resolution_falls_back_to_the_sensor_registry():
    """Filas viejas sin native_res_m en extra_meta: la resolución sale del registro
    de sensores, no de un valor por defecto que empate todo."""
    rows = [
        ("modis.tif", date(2026, 7, 28), {"sensor": "modis"}),
        ("s2.tif", date(2026, 7, 26), {"sensor": "s2"}),
    ]
    assert pick_sharpest(rows)[0] == "s2.tif"
