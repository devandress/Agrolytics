"""Cloud masking from the Sentinel-2 Scene Classification Layer.

A cloudy pixel averaged in as if it were canopy is what produced impossible
readings like NDVI = -0.002 on mid-season lettuce. These tests pin the two things
that must never regress: which SCL classes are thrown away, and that a scene
without an SCL band is left alone instead of getting an invented mask.
"""

import numpy as np
import pytest

from app.services.satellite_ingestion import (
    _SCL_DISCARD,
    _clear_mask,
    _s2_boa_offset,
    _to_reflectance,
)
from app.services.sensors import LANDSAT, to_reflectance


def test_no_scl_band_means_no_mask():
    """Landsat, MODIS and Sentinel-2 L1C carry no SCL — don't fabricate one."""
    assert _clear_mask(None) == (None, None)


def test_keeps_vegetation_soil_water_and_unclassified():
    # 4 vegetation · 5 bare soil · 6 water · 7 unclassified
    scl = np.array([[4, 5, 6, 7]], dtype=np.float32)
    clear, frac = _clear_mask(scl)
    assert clear.all()
    assert frac == 0.0


def test_discards_clouds_shadows_cirrus_and_nodata():
    # 0 nodata · 1 defective · 2 dark area · 3 cloud shadow · 8/9 cloud · 10 cirrus · 11 snow
    scl = np.array([[0, 1, 2, 3, 8, 9, 10, 11]], dtype=np.float32)
    clear, frac = _clear_mask(scl)
    assert not clear.any()
    assert frac == 1.0


def test_reports_the_discarded_fraction():
    scl = np.array([[4, 4, 9, 3], [5, 4, 8, 10], [4, 6, 4, 0]], dtype=np.float32)
    clear, frac = _clear_mask(scl)
    assert int(clear.sum()) == 7
    assert frac == round(5 / 12, 4)


def test_resampled_scl_codes_are_rounded_not_truncated():
    """The SCL is resampled onto the 10 m grid before masking; nearest-neighbour
    keeps codes integral, but float round-trips can leave 3.9999 — that pixel is
    class 4 (vegetation), and truncating would silently turn it into 3 (cloud
    shadow) and throw away good data."""
    scl = np.array([[3.9999, 8.0001]], dtype=np.float32)
    clear, _ = _clear_mask(scl)
    assert clear[0][0]         # 4 → se conserva
    assert not clear[0][1]     # 8 → se descarta


def test_discard_set_is_the_documented_one():
    assert _SCL_DISCARD == frozenset({0, 1, 2, 3, 8, 9, 10, 11})


# ── Conversión a reflectancia ──


class _FakeItem:
    def __init__(self, baseline):
        self.properties = {} if baseline is None else {"s2:processing_baseline": baseline}


def test_modern_baselines_carry_the_offset():
    assert _s2_boa_offset(_FakeItem("04.00")) == -1000.0
    assert _s2_boa_offset(_FakeItem("05.12")) == -1000.0


def test_old_baselines_do_not():
    """Escenas anteriores a 04.00 no llevan el desplazamiento; aplicarlo las rompería."""
    assert _s2_boa_offset(_FakeItem("03.01")) == 0.0


def test_missing_baseline_assumes_the_modern_one():
    assert _s2_boa_offset(_FakeItem(None)) == -1000.0
    assert _s2_boa_offset(_FakeItem("no-es-un-numero")) == -1000.0


def test_nodata_stays_nodata_through_the_offset():
    """El desplazamiento es aditivo: sin cuidado, un píxel sin dato (0) se
    convertiría en -0.1 y rompería la convención "0 = sin dato"."""
    arr = np.array([[0.0, 2238.0]], dtype=np.float32)
    out = _to_reflectance(arr, -1000.0)
    assert out[0][0] == 0.0
    assert out[0][1] == pytest.approx(0.1238, abs=1e-4)


def test_valid_pixels_land_in_a_physical_range():
    """Reflectancia de superficie: 0–1. Un azul de 0.17 sobre suelo agrícola era la
    señal de que faltaba el desplazamiento."""
    arr = np.array([[1729.0, 2238.0, 3024.0]], dtype=np.float32)
    out = _to_reflectance(arr, -1000.0)
    assert out.min() > 0.0 and out.max() < 1.0


# ── Escalado genérico por sensor ──
# Mismo error que el desplazamiento de Sentinel-2, en Landsat: `DN*2.75e-5 - 0.2`
# convertía los píxeles sin dato (0) en -0.2 de reflectancia.

def test_landsat_nodata_survives_its_negative_offset():
    arr = np.array([[0.0, 12000.0]], dtype=np.float32)
    out = to_reflectance(arr, LANDSAT.scale, LANDSAT.offset)
    assert out[0][0] == 0.0
    assert out[0][1] == pytest.approx(0.13, abs=1e-4)


def test_no_scale_leaves_the_array_untouched():
    """MODIS entrega NDVI/EVI ya calculados: no hay reflectancia que convertir."""
    arr = np.array([[0.0, 12000.0]], dtype=np.float32)
    assert to_reflectance(arr, None) is arr


def test_scaled_landsat_lands_in_a_physical_range():
    arr = np.array([[8000.0, 12000.0, 20000.0]], dtype=np.float32)
    out = to_reflectance(arr, LANDSAT.scale, LANDSAT.offset)
    assert out.min() > 0.0 and out.max() < 1.0
