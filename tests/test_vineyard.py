"""Unit tests for vineyard (Vid) support across the agronomic modules."""

from datetime import date

from app.services.anomaly import DEFAULT_THRESHOLDS, thresholds_for_crop
from app.services.pest_catalog import PEST_CATALOG, default_pests_for_crop
from app.services.pest_model import CROP_THREAT
from app.services.phenology import (
    assess_phenology,
    current_stage,
    is_vine,
    vine_progress,
)

_STAGE_LABELS = {
    "establecimiento", "crecimiento vegetativo", "llenado / madurez", "lista para cosecha",
}


def test_vid_has_distinct_thresholds():
    t = thresholds_for_crop("Vid")
    assert t["ndmi_warn"] == 0.20                       # tolerant / RDI-friendly
    assert t["ndmi_warn"] != DEFAULT_THRESHOLDS["ndmi_warn"]


def test_vid_pests_selected():
    keys = set(default_pests_for_crop("Vid"))
    # Shared fungal threats + the grape-specific additions must all map to Vid.
    for k in ("downy_mildew", "botrytis", "powdery_mildew",
              "lobesia", "vine_mealybug", "sharpshooter"):
        assert k in keys


def test_grape_specific_pest_params_valid():
    for key in ("lobesia", "vine_mealybug", "sharpshooter"):
        p = PEST_CATALOG[key]
        assert p["kind"] == "insect"
        assert "dd_base" in p and "dd_threshold" in p
        assert "Vid" in p["crops"]


def test_vid_primary_threat_present():
    assert "Vid" in CROP_THREAT
    assert CROP_THREAT["Vid"]["kind"] == "fungal"      # oídio (powdery mildew)


def test_vine_season_progress():
    assert is_vine("Vid")
    summer = vine_progress(date(2026, 7, 1))
    assert summer is not None and 0.0 < summer < 1.0   # mid-season
    assert vine_progress(date(2026, 1, 1)) is None      # dormant (winter)


def test_current_stage_vine_calendar_driven():
    # Dormant out of season, a real stage in season — no planting date needed.
    assert current_stage("Vid", None, date(2026, 1, 1)) == "dormancia"
    assert current_stage("Vid", None, date(2026, 7, 1)) in _STAGE_LABELS


def test_assess_phenology_vid_uses_season():
    out = assess_phenology([(date(2026, 6, 1), 0.55)], "Vid", None)
    assert out["crop"] == "Vid"
    assert "stage" in out                               # season-based, not planting-based
