"""Declarative registry of the satellite sensors AgroVision can ingest.

All sources are free and available through the Microsoft Planetary Computer STAC
API (signed URLs, no key) unless noted. Each sensor declares its STAC collection,
how to find each spectral band among the asset keys, its native spatial
resolution, and which indices it can produce.

This registry lets one generic ingestion path (``multisensor_ingestion``) serve
every optical sensor, and lets the fusion layer reason about resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field


@dataclass(frozen=True)
class Sensor:
    key: str                      # internal id, also used in COG filenames
    label: str                    # human label for the UI
    collection: str               # STAC collection id (Planetary Computer)
    kind: str                     # "optical" | "radar"
    native_res_m: int             # nominal spatial resolution in metres
    revisit_days: int             # typical revisit cadence
    # Candidate STAC asset keys per logical band (first match wins).
    bands: dict[str, list[str]] = dc_field(default_factory=dict)
    # Indices this sensor can compute given its bands.
    indices: tuple[str, ...] = ()
    requires_auth: bool = False   # True = needs NASA Earthdata, not auto-ingested
    enabled: bool = True
    # Scale factor to convert raw DN to reflectance (None = already reflectance).
    scale: float | None = None
    offset: float = 0.0


# Sentinel-2 surface reflectance — the high-resolution optical backbone.
SENTINEL2 = Sensor(
    key="s2", label="Sentinel-2", collection="sentinel-2-l2a", kind="optical",
    native_res_m=10, revisit_days=5,
    bands={
        "blue": ["blue", "B02", "B2"],
        "green": ["green", "B03", "B3"],
        "red": ["red", "B04", "B4"],
        "rededge": ["rededge1", "rededge", "B05", "B5"],
        "nir": ["nir", "nir08", "B08", "B8"],
        "swir": ["swir16", "swir-16", "B11"],
    },
    indices=("NDVI", "NDMI", "NDRE", "EVI"),
)

# Landsat 8/9 Collection-2 Level-2 — 30 m optical, complements Sentinel-2.
LANDSAT = Sensor(
    key="ls", label="Landsat 8/9", collection="landsat-c2-l2", kind="optical",
    native_res_m=30, revisit_days=8,
    bands={
        "blue": ["blue", "SR_B2"],
        "green": ["green", "SR_B3"],
        "red": ["red", "SR_B4"],
        "nir": ["nir08", "nir", "SR_B5"],
        "swir": ["swir16", "SR_B6"],
    },
    indices=("NDVI", "NDMI", "EVI"),
    # Landsat C2 L2 surface reflectance scaling: SR = DN*2.75e-5 - 0.2
    scale=2.75e-5, offset=-0.2,
)

# MODIS 16-day 250 m vegetation indices — coarse but frequent (temporal density).
MODIS = Sensor(
    key="modis", label="MODIS 250 m", collection="modis-13Q1-061", kind="optical",
    native_res_m=250, revisit_days=16,
    # MODIS 13Q1 ships NDVI/EVI directly as scaled int16 (×1e-4).
    bands={"ndvi": ["250m_16_days_NDVI", "NDVI"], "evi": ["250m_16_days_EVI", "EVI"]},
    indices=("NDVI", "EVI"),
    scale=1e-4,
)

# Sentinel-1 RTC radar handled by the dedicated radar_ingestion pipeline.
SENTINEL1 = Sensor(
    key="s1", label="Sentinel-1 (radar)", collection="sentinel-1-rtc", kind="radar",
    native_res_m=10, revisit_days=6, indices=("RVI", "VHVV"),
)

# Daily coarse sources — require NASA Earthdata auth, documented not auto-run.
VIIRS = Sensor(
    key="viirs", label="VIIRS (diario)", collection="viirs-13a1", kind="optical",
    native_res_m=500, revisit_days=1, indices=("NDVI",), requires_auth=True, enabled=False,
)

# Registry keyed by sensor.key
REGISTRY: dict[str, Sensor] = {s.key: s for s in (SENTINEL2, LANDSAT, MODIS, SENTINEL1, VIIRS)}

# Optical sensors that the generic multisensor ingestion will pull automatically.
AUTO_OPTICAL = [s for s in (SENTINEL2, LANDSAT, MODIS) if s.enabled]

# The high-resolution backbone all other sensors are normalised toward.
BACKBONE_KEY = "s2"


def get_sensor(key: str) -> Sensor | None:
    return REGISTRY.get(key)


def find_asset(item_assets: dict, candidates: list[str]) -> str | None:
    """Return the href of the first matching asset key, or None."""
    for key in candidates:
        if key in item_assets:
            return item_assets[key].href
    return None
