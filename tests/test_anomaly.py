"""Unit tests for the anomaly-detection service (pure functions, no DB)."""

from datetime import date, timedelta

import pytest

from app.services import anomaly


# ── anomaly_score ─────────────────────────────────────────────────────────────
def test_anomaly_score_basic():
    assert anomaly.anomaly_score(8.0, 10.0, 2.0) == pytest.approx(-1.0)


def test_anomaly_score_zero_std_returns_zero():
    assert anomaly.anomaly_score(5.0, 10.0, 0.0) == 0.0


# ── classify boundaries ───────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "z, trend, expected",
    [
        (0.0, False, "ok"),
        (-1.4, False, "ok"),
        (-1.6, False, "warning"),
        (-2.0, False, "warning"),
        (-2.6, False, "alert"),
        (-0.5, True, "alert"),     # downtrend forces alert regardless of z
    ],
)
def test_classify(z, trend, expected):
    assert anomaly.classify(z, trend) == expected


# ── detect_trend ──────────────────────────────────────────────────────────────
def test_detect_trend_three_consecutive_declines():
    assert anomaly.detect_trend([0.8, 0.7, 0.6, 0.5]) is True


def test_detect_trend_insufficient_decline():
    assert anomaly.detect_trend([0.8, 0.7, 0.6]) is False  # only 2 declining steps


def test_detect_trend_resets_on_increase():
    # decline, decline, rise, decline -> longest run is 2, not enough
    assert anomaly.detect_trend([0.8, 0.7, 0.6, 0.65, 0.6]) is False


def test_detect_trend_flat_is_not_decline():
    assert anomaly.detect_trend([0.5, 0.5, 0.5, 0.5]) is False


# ── compute_baseline ──────────────────────────────────────────────────────────
def test_compute_baseline_groups_by_index_type():
    readings = [
        {"index_type": "NDVI", "value": 0.4},
        {"index_type": "NDVI", "value": 0.6},
        {"index_type": "NDMI", "value": 0.2},
        {"index_type": "NDVI", "value": None},  # skipped
    ]
    baseline = anomaly.compute_baseline(readings)
    assert baseline["NDVI"]["mean"] == pytest.approx(0.5)
    assert baseline["NDVI"]["n"] == 2
    assert baseline["NDVI"]["std"] == pytest.approx(0.1)
    assert baseline["NDMI"]["std"] == 0.0  # single value


# ── crop thresholds ───────────────────────────────────────────────────────────
def test_thresholds_for_known_crop_case_insensitive():
    t = anomaly.thresholds_for_crop("lechuga")
    assert t["ndmi_crit"] == 0.30
    assert t["salinity_crit"] == 3.0


def test_thresholds_for_unknown_crop_falls_back_to_default():
    assert anomaly.thresholds_for_crop("Maíz") == anomaly.DEFAULT_THRESHOLDS
    assert anomaly.thresholds_for_crop(None) == anomaly.DEFAULT_THRESHOLDS


def test_evaluate_field_thresholds_statuses():
    result = anomaly.evaluate_field_thresholds(
        "Lechuga",
        {"NDMI": 0.29, "NDRE": 0.43, "SALINITY": 3.5},
    )
    assert result["NDMI"]["status"] == "critical"   # 0.29 <= 0.30 crit
    assert result["NDRE"]["status"] == "ok"         # 0.43 > 0.42 warn
    assert result["SALINITY"]["status"] == "critical"  # 3.5 >= 3.0 crit


def test_evaluate_field_thresholds_warning_band():
    result = anomaly.evaluate_field_thresholds("Lechuga", {"NDMI": 0.40})
    assert result["NDMI"]["status"] == "warning"    # crit(0.30) < 0.40 <= warn(0.45)


# ── analyze_index end-to-end ──────────────────────────────────────────────────
def test_analyze_index_flags_alert_on_downtrend():
    today = date(2026, 6, 1)
    history = [(today + timedelta(weeks=i), v) for i, v in enumerate([0.8, 0.7, 0.6, 0.5])]
    result = anomaly.analyze_index(history)
    assert result["trend"] is True
    assert result["status"] == "alert"


def test_analyze_index_insufficient_data():
    assert anomaly.analyze_index([])["status"] == "insufficient_data"
