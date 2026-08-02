"""Unit tests for spectral and radar index formulas (no DB / rasters required)."""

import math

import numpy as np
import pytest

from app.services import indices


# ── Scalar correctness ──────────────────────────────────────────────────────
def test_ndvi_known_value():
    # (0.5 - 0.1) / (0.5 + 0.1) = 0.4 / 0.6
    assert indices.ndvi(0.5, 0.1) == pytest.approx(0.4 / 0.6)


def test_ndmi_and_ndre_use_normalized_difference():
    assert indices.ndmi(0.4, 0.2) == pytest.approx(0.2 / 0.6)
    assert indices.ndre(0.4, 0.2) == pytest.approx(0.2 / 0.6)


def test_salinity_index():
    assert indices.salinity_index(0.2, 0.5) == pytest.approx(math.sqrt(0.1))


def test_salinity_index_clamps_negative_product():
    # Negative reflectance must not produce NaN.
    assert indices.salinity_index(-0.2, 0.5) == 0.0


def test_evi_known_value():
    nir, red, blue = 0.5, 0.1, 0.05
    expected = 2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)
    assert indices.evi(nir, red, blue) == pytest.approx(expected)


def test_rvi_known_value():
    # 4 * 0.1 / (0.3 + 0.1) = 1.0
    assert indices.rvi(0.3, 0.1) == pytest.approx(1.0)


def test_vh_vv_ratio():
    assert indices.vh_vv_ratio(0.4, 0.1) == pytest.approx(0.25)


# ── Divide-by-zero / nodata handling ─────────────────────────────────────────
def test_normalized_difference_zero_denominator_returns_zero():
    assert indices.ndvi(0.0, 0.0) == 0.0
    assert indices.rvi(0.0, 0.0) == 0.0
    assert indices.vh_vv_ratio(0.0, 0.1) == 0.0


def test_clipping_to_valid_range():
    # NDVI is clipped to [-1, 1]; pathological inputs cannot exceed it.
    assert -1.0 <= indices.ndvi(0.0, 1.0) <= 1.0


# ── Array support ─────────────────────────────────────────────────────────────
def test_ndvi_array():
    nir = np.array([0.5, 0.0, 0.8], dtype=np.float32)
    red = np.array([0.1, 0.0, 0.2], dtype=np.float32)
    out = indices.ndvi(nir, red)
    assert isinstance(out, np.ndarray)
    assert out.shape == nir.shape
    assert out[1] == 0.0  # 0/0 -> 0
    assert out[0] == pytest.approx(0.4 / 0.6, abs=1e-6)


def test_rvi_array_clipped_0_to_4():
    vv = np.array([0.3, 0.0], dtype=np.float32)
    vh = np.array([0.1, 0.0], dtype=np.float32)
    out = indices.rvi(vv, vh)
    assert out[1] == 0.0
    assert np.all(out >= 0.0) and np.all(out <= 4.0)


# ── Unidades: por qué el EVI de Sentinel-2 estaba mal ──
# Los assets de Sentinel-2 L2A son enteros escalados (reflectancia = DN/10000). Las
# diferencias normalizadas sobreviven a la unidad equivocada porque el factor se
# cancela; el EVI no, porque su fórmula tiene constantes absolutas.

_S2_SCALE = 1e-4


def test_normalized_differences_are_scale_invariant():
    nir, red, swir = 3000.0, 1200.0, 1800.0
    assert indices.ndvi(nir, red) == pytest.approx(indices.ndvi(nir * _S2_SCALE, red * _S2_SCALE))
    assert indices.ndmi(nir, swir) == pytest.approx(indices.ndmi(nir * _S2_SCALE, swir * _S2_SCALE))
    assert indices.ndre(nir, red) == pytest.approx(indices.ndre(nir * _S2_SCALE, red * _S2_SCALE))


def test_evi_is_not_scale_invariant():
    """Si esto empieza a pasar, alguien quitó las constantes de la fórmula del EVI."""
    nir, red, blue = 3000.0, 1200.0, 600.0
    crudo = indices.evi(nir, red, blue)
    escalado = indices.evi(nir * _S2_SCALE, red * _S2_SCALE, blue * _S2_SCALE)
    assert crudo != pytest.approx(escalado)


def test_evi_on_reflectance_stays_below_ndvi_for_this_canopy():
    """Con reflectancia bien escalada el EVI queda por debajo del NDVI en dosel
    ralo. Sobre enteros daba 0.79 contra un NDVI de 0.43 — físicamente imposible y
    la señal de que la cuenta estaba en la unidad equivocada."""
    nir, red, blue = 3000.0 * _S2_SCALE, 1200.0 * _S2_SCALE, 600.0 * _S2_SCALE
    assert indices.evi(nir, red, blue) < indices.ndvi(nir, red)


def test_evi_on_raw_integers_is_implausibly_high():
    nir, red, blue = 3000.0, 1200.0, 600.0
    assert indices.evi(nir, red, blue) > indices.ndvi(nir, red)


# ── Desplazamiento BOA de Sentinel-2 ──
# Desde la baseline 04.00 las bandas traen un -1000 que Planetary Computer no aplica.
# Es aditivo, así que NO se cancela en una diferencia normalizada: afecta al NDVI.

def test_additive_offset_changes_normalized_difference():
    """Contraste con la invariancia de escala: multiplicar no cambia el NDVI,
    sumar sí. Por eso el desplazamiento faltante se veía como sesgo entre satélites."""
    nir, red = 3024.0, 2238.0
    sin_offset = indices.ndvi(nir * 1e-4, red * 1e-4)
    con_offset = indices.ndvi((nir - 1000) * 1e-4, (red - 1000) * 1e-4)
    assert sin_offset != pytest.approx(con_offset)
    # Con los valores reales de la escena verificada: 0.150 -> 0.243
    assert sin_offset == pytest.approx(0.150, abs=0.005)
    assert con_offset == pytest.approx(0.243, abs=0.005)


def test_offset_moves_sentinel2_toward_landsat():
    """El desplazamiento acerca Sentinel-2 a Landsat (0.270 medido en el mismo
    campo). Si esto deja de cumplirse, alguien tocó el escalado."""
    nir, red, landsat = 3024.0, 2238.0, 0.270
    sin_offset = indices.ndvi(nir * 1e-4, red * 1e-4)
    con_offset = indices.ndvi((nir - 1000) * 1e-4, (red - 1000) * 1e-4)
    assert abs(con_offset - landsat) < abs(sin_offset - landsat)
