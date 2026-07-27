"""Unit tests for the stage-aware crop-water (irrigation) service."""

from app.services.irrigation import (
    daily_water_need,
    effective_rainfall,
    is_vine,
    kc_for,
    rdi_active,
)


def test_kc_is_stage_aware():
    # Vegetables peak at canopy closure (mid); vines run much lower.
    assert kc_for("Lechuga", "llenado / madurez") == 1.00
    assert kc_for("Lechuga", "establecimiento") == 0.70
    assert kc_for("Vid", "llenado / madurez") == 0.70


def test_kc_unknown_crop_falls_back():
    assert kc_for("Quinoa", "llenado / madurez") == 1.00  # default mid


def test_effective_rainfall():
    assert effective_rainfall(2) == 0.0          # tiny events lost
    assert effective_rainfall(10) == 8.0         # 80% effective
    assert effective_rainfall(None) == 0.0


def test_is_vine_aliases():
    assert is_vine("Vid") and is_vine("uva") and is_vine("GRAPE")
    assert not is_vine("Lechuga")


def test_rdi_only_for_vine_in_ripening():
    assert rdi_active("Vid", "llenado / madurez")
    assert rdi_active("Vid", "lista para cosecha")
    assert not rdi_active("Vid", "establecimiento")     # pre-veraison: vine needs water
    assert not rdi_active("Lechuga", "llenado / madurez")


def test_water_need_vegetable_no_rain():
    wb = daily_water_need(5.0, "Lechuga", "llenado / madurez", 0.0)
    assert wb["etc_mm"] == 5.0 and wb["net_irrigation_mm"] == 5.0
    assert wb["rdi"] is False


def test_water_need_rain_covers_demand():
    wb = daily_water_need(5.0, "Lechuga", "llenado / madurez", 10.0)
    assert wb["effective_rain_mm"] == 8.0
    assert wb["net_irrigation_mm"] == 0.0        # rain covers ETc


def test_vine_rdi_reduces_target():
    full = daily_water_need(5.0, "Vid", "crecimiento vegetativo", 0.0)   # no RDI
    rdi = daily_water_need(5.0, "Vid", "llenado / madurez", 0.0)         # RDI applies
    assert rdi["rdi"] is True
    # Ripening target is intentionally below the plain ETc of the same crop/ET0.
    assert rdi["target_etc_mm"] < rdi["etc_mm"]
    assert rdi["net_irrigation_mm"] < full["etc_mm"]
