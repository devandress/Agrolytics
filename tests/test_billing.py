"""Unit tests for the subscription plan logic (no DB needed)."""

from app.services.plans import (
    MXN_MINIMUM,
    ORDER,
    PLANS,
    get_plan,
    plan_allows_ai,
    plan_max_fields,
    price_mxn_for_ha,
)


def test_catalog_integrity():
    assert ORDER == ["free", "pro", "enterprise"]
    for key in ORDER:
        p = PLANS[key]
        for field in ("key", "name", "price_label", "max_fields", "ai_monthly", "features"):
            assert field in p, f"{key} missing {field}"


def test_get_plan_defaults_to_free():
    assert get_plan(None)["key"] == "free"
    assert get_plan("does-not-exist")["key"] == "free"
    assert get_plan("pro")["key"] == "pro"


def test_ai_gating_by_plan():
    assert plan_allows_ai("free") is False
    assert plan_allows_ai("pro") is True
    assert plan_allows_ai("enterprise") is True


def test_field_limits_by_plan():
    # pro/enterprise are metered by hectares (billing.py), not by field count.
    assert plan_max_fields("free") == 1
    assert plan_max_fields("pro") is None
    assert plan_max_fields("enterprise") is None   # unlimited


def test_price_mxn_for_ha_free_is_flat_zero():
    price = price_mxn_for_ha("free", 2)
    assert price["mxn_month"] == 0
    assert price["over_ha_limit"] is False
    assert price_mxn_for_ha("free", 10)["over_ha_limit"] is True


def test_price_mxn_for_ha_enterprise_is_custom():
    price = price_mxn_for_ha("enterprise", 500)
    assert price["mxn_month"] is None
    assert price["billing"] == "custom"


def test_price_mxn_for_ha_pro_floors_at_minimum():
    price = price_mxn_for_ha("pro", 1)
    assert price["mxn_month"] == MXN_MINIMUM
    assert price["at_minimum"] is True


def test_price_mxn_for_ha_pro_applies_volume_discount_past_20ha():
    from app.services.plans import MXN_PER_HA, MXN_PER_HA_SCALE
    at_20 = price_mxn_for_ha("pro", 20)["mxn_month"]
    at_30 = price_mxn_for_ha("pro", 30)["mxn_month"]
    marginal = at_30 - at_20
    # The next 10 ha must bill at the scale rate, not the (higher) base rate.
    assert round(10 * MXN_PER_HA_SCALE) <= marginal < round(10 * MXN_PER_HA)
