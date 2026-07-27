"""Unit tests for the deep pest model + catalog (no DB/network needed)."""

from app.services.pest_catalog import PEST_CATALOG, default_pests_for_crop
from app.services.pest_model import assess_pest, assess_pests


def test_catalog_has_params():
    for key, p in PEST_CATALOG.items():
        assert "kind" in p and p["kind"] in ("fungal", "insect")
        assert "name" in p and "crops" in p


def test_default_pests_for_crop():
    keys = default_pests_for_crop("Brócoli")
    assert "dbm" in keys  # diamondback moth targets brassicas


def test_fungal_high_risk_with_leaf_wetness():
    p = PEST_CATALOG["downy_mildew"]  # temp_opt 10-20, rh_min 90, wet_hours 4
    r = assess_pest(p, temp_c=15, humidity=95, wet_hours=6, degree_days=None)
    assert r["level"] == "alto"
    assert r["score"] >= 70


def test_fungal_low_risk_when_dry():
    p = PEST_CATALOG["downy_mildew"]
    r = assess_pest(p, temp_c=15, humidity=30, wet_hours=0, degree_days=None)
    assert r["level"] == "bajo"


def test_insect_risk_scales_with_degree_days():
    p = PEST_CATALOG["dbm"]  # dd_threshold 180, temp_opt 20-30
    low = assess_pest(p, temp_c=25, humidity=50, wet_hours=0, degree_days=50)
    high = assess_pest(p, temp_c=25, humidity=50, wet_hours=0, degree_days=200)
    assert high["score"] > low["score"]


def test_assess_pests_sorted_desc():
    pests = [PEST_CATALOG["downy_mildew"], PEST_CATALOG["dbm"]]
    out = assess_pests(pests, temp_c=15, humidity=95, wet_hours=6, degree_days=10)
    assert out[0]["score"] >= out[1]["score"]
