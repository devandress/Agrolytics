"""Unit tests for the cross-sensor fusion math (no rasters/DB needed)."""

from datetime import date

import numpy as np

from app.services.fusion import (
    apply_gain_offset,
    build_pairs,
    fuse_gap,
    normalize_gain_offset,
    resample_to_grid,
)


def test_resample_upscale_shape():
    coarse = np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)
    out = resample_to_grid(coarse, (4, 4))
    assert out.shape == (4, 4)


def test_resample_identity():
    a = np.random.rand(5, 5).astype(np.float32)
    np.testing.assert_array_equal(resample_to_grid(a, (5, 5)), a)


def test_normalize_recovers_known_linear_relation():
    # backbone = 0.8 * source + 0.05
    source = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    backbone = 0.8 * source + 0.05
    gain, offset = normalize_gain_offset(source, backbone)
    assert abs(gain - 0.8) < 1e-6
    assert abs(offset - 0.05) < 1e-6


def test_normalize_identity_when_insufficient_pairs():
    assert normalize_gain_offset([0.3], [0.4]) == (1.0, 0.0)


def test_apply_gain_offset():
    out = apply_gain_offset(np.array([0.0, 1.0]), 2.0, 0.5)
    np.testing.assert_allclose(out, [0.5, 2.5])


def test_fuse_gap_adds_coarse_delta():
    hr0 = np.full((4, 4), 0.5, dtype=np.float32)
    coarse_t0 = np.full((2, 2), 0.5, dtype=np.float32)
    coarse_t1 = np.full((2, 2), 0.6, dtype=np.float32)  # +0.1 change
    fused = fuse_gap(hr0, coarse_t0, coarse_t1)
    assert fused.shape == (4, 4)
    assert np.allclose(fused, 0.6, atol=1e-4)


def test_build_pairs_matches_near_coincident_dates():
    backbone = [(date(2026, 3, 1), 0.5), (date(2026, 3, 20), 0.7)]
    source = [(date(2026, 3, 3), 0.45), (date(2026, 6, 1), 0.9)]  # 2nd too far
    src, bb = build_pairs(backbone, source, max_day_gap=8)
    assert src == [0.45]
    assert bb == [0.5]
