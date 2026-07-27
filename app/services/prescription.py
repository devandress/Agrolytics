"""Nitrogen prescription service.

Derives a variable-rate nitrogen map by applying zone-specific dose fractions
to a base application rate.  The output is a GeoJSON FeatureCollection that
mimics the structure of an ISO 11783-10 (ISO-XML) prescription file.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# Zone → fraction of the base nitrogen dose
_ZONE_DOSE: dict[str, float] = {
    "low": 1.20,     # under-performing zones need more N
    "medium": 1.00,
    "high": 0.70,    # high-performing zones need less N
}

# Default base dose by crop type (kg N / ha)
_DEFAULT_BASE_DOSE: dict[str, float] = {
    "corn": 180.0,
    "wheat": 140.0,
    "soy": 0.0,      # soy fixes nitrogen — no supplemental N
    "rice": 100.0,
    "sunflower": 90.0,
    "vid": 40.0,     # wine grapes need little N; excess hurts fruit quality
    "grape": 40.0,
    "default": 120.0,
}


def base_dose_for(crop_type: str | None) -> float:
    """Default base nitrogen dose (kg N/ha) for *crop_type* (case-insensitive)."""
    return _DEFAULT_BASE_DOSE.get((crop_type or "default").lower(), _DEFAULT_BASE_DOSE["default"])


def generate_prescription(
    zone_map: dict[str, Any],
    crop_type: str | None,
    base_dose_override: float | None = None,
) -> dict[str, Any]:
    """Build a variable-rate nitrogen prescription from a zone-map GeoJSON.

    Args:
        zone_map:           GeoJSON FeatureCollection produced by clustering.
        crop_type:          Crop name for default base-dose lookup.
        base_dose_override: If provided, overrides the default base dose (kg/ha).

    Returns:
        A GeoJSON FeatureCollection with prescription attributes, plus a top-level
        ``summary`` section that mirrors an ISO-XML prescription envelope.

    Raises:
        ValueError: If *zone_map* is not a valid FeatureCollection.
    """
    if zone_map.get("type") != "FeatureCollection":
        raise ValueError("zone_map must be a GeoJSON FeatureCollection.")

    crop_key = (crop_type or "default").lower()
    base_dose = base_dose_override or _DEFAULT_BASE_DOSE.get(crop_key, _DEFAULT_BASE_DOSE["default"])

    logger.info(f"Generating prescription for crop={crop_key}, base_dose={base_dose} kg N/ha")

    rx_features: list[dict[str, Any]] = []
    zone_summary: list[dict[str, Any]] = []

    for feature in zone_map.get("features", []):
        zone = feature.get("properties", {}).get("zone", "medium")
        fraction = _ZONE_DOSE.get(zone, 1.0)
        dose = round(base_dose * fraction, 1)

        rx_feature = {
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                "zone": zone,
                "base_dose_kg_ha": base_dose,
                "dose_fraction": fraction,
                "prescribed_dose_kg_ha": dose,
                "product": "Urea (46-0-0)",
                "unit": "kg/ha",
            },
        }
        rx_features.append(rx_feature)
        zone_summary.append({"zone": zone, "dose_kg_ha": dose})

    prescription = {
        "type": "FeatureCollection",
        "features": rx_features,
        "summary": {
            "iso_xml_type": "PDV",           # Prescription Data Value
            "operation": "Fertilization",
            "product": "Nitrogen (N)",
            "unit": "kg/ha",
            "crop": crop_key,
            "base_dose_kg_ha": base_dose,
            "zone_doses": zone_summary,
            "note": "Simulated ISO 11783-10 prescription — replace with certified export.",
        },
    }
    return prescription
