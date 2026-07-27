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
