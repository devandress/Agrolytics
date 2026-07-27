"""Unit tests for the subscription plan logic (no DB needed)."""

from app.services.plans import (
    ORDER,
    PLANS,
    get_plan,
    plan_allows_ai,
    plan_max_fields,
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
    assert plan_max_fields("free") == 1
    assert plan_max_fields("pro") == 10
    assert plan_max_fields("enterprise") is None   # unlimited
