"""Declarative registry of the satellite sensors Agrolytics can ingest.

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

# CBERS-4A/MUX (chino-brasileño, INPE) — 16 m con revisita de 5 días, la misma
# cadencia que Sentinel-2 y muy por encima de Landsat. En un lote de 19 ha son ~750
# píxeles: sirve de verdad para zonificar.
#
# Apagado por tres motivos concretos, no por descarte:
#   1. No está en Planetary Computer. Vive en el STAC del INPE / Brazil Data Cube,
#      así que necesita un cliente aparte del que usa el resto de la ingesta.
#   2. Sólo trae azul, verde, rojo e infrarrojo cercano: alcanza para NDVI y EVI,
#      pero NO para NDMI (falta SWIR) ni NDRE (falta red-edge).
#   3. La cobertura del catálogo está centrada en Sudamérica. Antes de encenderlo hay
#      que verificar que haya escenas sobre el norte de México, que es el mercado.
# (La cámara WPM del mismo satélite da 8 m multiespectral y 2 m pancromático, pero
# con revisita de 31 días: demasiado espaciada para seguir un cultivo.)
CBERS4A_MUX = Sensor(
    key="cbers4a", label="CBERS-4A/MUX (16 m)", collection="CBERS4A-MUX-L4-SR-1",
    kind="optical", native_res_m=16, revisit_days=5,
    indices=("NDVI", "EVI"), enabled=False,
)

# Gaofen-6/WFV (China) — 16 m con revisita de 4 días, más frecuente que Sentinel-2.
# Es el único de esta lista diseñado explícitamente para agricultura de precisión, y
# el único fuera de Sentinel-2 que trae RED-EDGE: dos bandas en 0.69–0.77 µm más una
# amarilla en 0.59–0.63 µm. Eso habilita NDRE, que hoy sólo puede calcular Sentinel-2
# (por eso NDRE tiene 24 fechas donde NDVI tiene 37).
#
# Apagado por el acceso, no por las especificaciones: los datos se distribuyen vía
# CRESDA con registro, no con descarga abierta como Copernicus o USGS. Antes de
# invertir en la integración hay que resolver si se consigue acceso programático
# desde México; sin eso, las especificaciones son irrelevantes.
GAOFEN6_WFV = Sensor(
    key="gf6", label="Gaofen-6/WFV (16 m)", collection="GF6-WFV", kind="optical",
    native_res_m=16, revisit_days=4,
    indices=("NDVI", "NDRE", "EVI"), requires_auth=True, enabled=False,
)

# Registry keyed by sensor.key
REGISTRY: dict[str, Sensor] = {
    s.key: s for s in (SENTINEL2, LANDSAT, MODIS, SENTINEL1, VIIRS, CBERS4A_MUX, GAOFEN6_WFV)
}

# Optical sensors that the generic multisensor ingestion will pull automatically.
AUTO_OPTICAL = [s for s in (SENTINEL2, LANDSAT, MODIS) if s.enabled]

# The high-resolution backbone all other sensors are normalised toward.
BACKBONE_KEY = "s2"

# Legacy rows stored the STAC *collection* id where the rest of the system expects
# the registry *key*. Reading through this map keeps those observations usable —
# without it Sentinel-1 shows up nameless in the UI and drops out of the
# next-overpass list, because REGISTRY.get() never matches.
_COLLECTION_TO_KEY: dict[str, str] = {s.collection: s.key for s in REGISTRY.values()}


def normalize_sensor_key(value: str | None) -> str:
    """Registry key for *value*, accepting either a key or a STAC collection id."""
    if not value:
        return BACKBONE_KEY  # filas del pipeline original, anteriores al campo sensor
    if value in REGISTRY:
        return value
    return _COLLECTION_TO_KEY.get(value, value)


# Por debajo de esto el "mapa" del lote es un puñado de cuadrados: no se puede ver
# una zona seca, ni un foco de plaga, ni nada que justifique llamarlo agricultura de
# precisión. Es una cota deliberadamente baja — sólo descarta lo que no sirve para
# nada, no lo que sirve poco.
MIN_USEFUL_PIXELS = 50


def pixels_for_field(sensor: Sensor, area_ha: float) -> float:
    """Cuántos píxeles de *sensor* caben en un lote de *area_ha* hectáreas."""
    if area_ha <= 0:
        return 0.0
    return (area_ha * 10_000.0) / float(sensor.native_res_m**2)


def to_reflectance(arr, scale: float | None, offset: float = 0.0):
    """DN → reflectancia, conservando el 0 como "sin dato".

    El detalle que importa es el orden. Un desplazamiento aditivo aplicado a ciegas
    convierte el 0 de "sin dato" en un valor real: con Landsat (``DN*2.75e-5 - 0.2``)
    los píxeles vacíos pasaban a valer −0.2 de reflectancia. Después el remuestreo
    interpola esos −0.2 contra píxeles buenos y ensucia el borde de la parcela con
    reflectancias que nadie midió — y como el índice resultante ya no da exactamente
    0, sobrevive al filtro ``!= 0`` y entra en el promedio.

    Todo el sistema usa 0 = sin dato (las validaciones ``> 0``, el colormap, el
    recorte de rásters). Esta función mantiene esa convención.
    """
    if not scale:
        return arr
    nodata = arr == 0
    out = arr * scale + offset
    out[nodata] = 0.0
    return out


def useful_for_field(sensor: Sensor, area_ha: float | None,
                     min_pixels: int = MIN_USEFUL_PIXELS) -> bool:
    """¿Este sensor aporta algo en un lote de este tamaño?

    MODIS a 250 m cubre un lote de 19 ha en **3 píxeles**. Eso no es un mapa: es un
    número con forma de imagen, y encima contamina la serie porque llega con otra
    calibración y otra fecha. La regla es por tamaño, no por sensor: en un rancho de
    600 ha MODIS sí aporta (casi 100 píxeles) y ahí se ingiere igual.

    Sin superficie conocida se responde ``True``: ante la duda, no se descarta dato.
    """
    if area_ha is None:
        return True
    return pixels_for_field(sensor, area_ha) >= min_pixels


def get_sensor(key: str) -> Sensor | None:
    return REGISTRY.get(key)


def find_asset(item_assets: dict, candidates: list[str]) -> str | None:
    """Return the href of the first matching asset key, or None."""
    for key in candidates:
        if key in item_assets:
            return item_assets[key].href
    return None
