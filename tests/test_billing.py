"""Unit tests for the subscription plan logic (no DB needed)."""

from app.services.plans import (
    MXN_MINIMUM,
    ORDER,
    PLANS,
    get_plan,
    plan_allows_ai,
    plan_allows_export,
    plan_allows_index,
    plan_allows_radar_fusion,
    plan_max_fields,
    plan_max_ha,
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
    # Free is limited by field COUNT, not area — a realistic 40 ha block must not
    # be rejected (that made the free tier unusable for this market).
    assert price_mxn_for_ha("free", 40)["over_ha_limit"] is False


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


def test_no_plan_caps_area():
    # Area caps are deliberately absent — see FREE_MAX_HA comment in plans.py.
    assert plan_max_ha("free") is None
    assert plan_max_ha("pro") is None
    assert plan_max_ha("enterprise") is None


def test_plan_allows_index_matches_catalog():
    assert plan_allows_index("free", "NDVI") is True
    assert plan_allows_index("free", "ndmi") is True  # case-insensitive
    assert plan_allows_index("free", "NDRE") is False
    assert plan_allows_index("pro", "NDRE") is True
    assert plan_allows_index("enterprise", "VHVV") is True


def test_plan_allows_radar_fusion_and_export():
    assert plan_allows_radar_fusion("free") is False
    assert plan_allows_radar_fusion("pro") is True
    assert plan_allows_export("free") is False
    assert plan_allows_export("pro") is True


# ── El webhook: único código que otorga o quita un plan pago ──
# Antes de esto, la ruta del dinero no tenía un solo test. Se prueba la decisión
# pura; el endpoint sólo busca al usuario y guarda.

import uuid  # noqa: E402

from app.api.v1.endpoints.billing import decide_subscription_change  # noqa: E402

_UID = uuid.uuid4()


def _pre(status, ref=None, **extra):
    return {"status": status, "external_reference": ref if ref is not None else f"{_UID}:pro", **extra}


def test_authorized_grants_the_plan_in_the_reference():
    d = decide_subscription_change(_pre("authorized"))
    assert d["action"] == "grant"
    assert d["plan"] == "pro"
    assert d["user_id"] == _UID


def test_cancelled_revokes():
    assert decide_subscription_change(_pre("cancelled"))["action"] == "revoke"


def test_paused_revokes_too():
    """Una tarjeta que deja de funcionar pausa la suscripción: si eso no revocara,
    el plan pago seguiría activo sin que entre dinero."""
    assert decide_subscription_change(_pre("paused"))["action"] == "revoke"


def test_pending_changes_nothing():
    """Autorización a medio camino. Otorgar acá sería regalar el plan."""
    assert decide_subscription_change(_pre("pending"))["action"] == "none"


def test_an_unknown_future_status_changes_nothing():
    """De las dos formas de equivocarse ante un estado que no conocemos, regalar un
    plan pago es la cara. Si MercadoPago agrega un estado, no se toca el plan."""
    assert decide_subscription_change(_pre("charged_back"))["action"] == "none"


def test_a_reference_without_separator_is_rejected():
    assert "reason" in decide_subscription_change(_pre("authorized", ref="basura"))


def test_a_non_uuid_user_is_rejected():
    assert "reason" in decide_subscription_change(_pre("authorized", ref="no-soy-uuid:pro"))


def test_an_unknown_plan_is_rejected():
    """El plan sale de la referencia; sólo se aceptan los del catálogo, así que una
    referencia manipulada no puede inventar un plan con límites mayores."""
    d = decide_subscription_change(_pre("authorized", ref=f"{_UID}:ilimitado"))
    assert d["reason"] == "bad plan"


def test_a_missing_reference_is_rejected():
    assert "reason" in decide_subscription_change({"status": "authorized"})


def test_the_plan_comes_from_the_reference_not_from_the_payload():
    """Si el plan se leyera de un campo suelto del cuerpo, bastaría con mandar
    `plan: enterprise`. Sale de external_reference, que fijamos nosotros al crear
    el preapproval."""
    d = decide_subscription_change(_pre("authorized", ref=f"{_UID}:free", plan="enterprise"))
    assert d["plan"] == "free"
