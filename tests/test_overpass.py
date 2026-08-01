"""Próximo paso satelital: aritmética de ciclo de repetición, sin órbitas."""

from datetime import date

from app.services.overpass import next_pass


def test_one_cycle_after_the_last_image():
    # Sentinel-2 pasa cada 5 días: visto el 20, vuelve el 25.
    assert next_pass(date(2026, 7, 20), 5, today=date(2026, 7, 21)) == date(2026, 7, 25)


def test_never_returns_a_date_in_the_past():
    """Si hubo nubes varios pasos seguidos, la última imagen puede ser vieja. El
    usuario necesita el PRÓXIMO paso, no uno que ya ocurrió."""
    nxt = next_pass(date(2026, 6, 1), 5, today=date(2026, 7, 20))
    assert nxt >= date(2026, 7, 20)


def test_lands_on_the_cycle_grid():
    """Adelantar hasta hoy no debe correr la fase de la órbita: el próximo paso
    sigue cayendo en un múltiplo del ciclo desde la última imagen."""
    last, revisit = date(2026, 6, 1), 5
    nxt = next_pass(last, revisit, today=date(2026, 7, 20))
    assert (nxt - last).days % revisit == 0


def test_today_still_counts_as_upcoming():
    """Un paso que cae hoy todavía no ocurrió para el usuario: no se salta."""
    assert next_pass(date(2026, 7, 15), 5, today=date(2026, 7, 20)) == date(2026, 7, 20)


def test_landsat_cadence_is_slower_than_sentinel():
    d, today = date(2026, 7, 1), date(2026, 7, 2)
    assert next_pass(d, 16, today) > next_pass(d, 5, today)


# ── Claves de sensor ──
# La ingesta de radar guardaba la colección STAC ("sentinel-1-rtc") donde el resto
# del sistema espera la clave del registro ("s1"). Con eso, REGISTRY.get() no
# encontraba nada: el radar salía sin nombre en la interfaz y desaparecía del aviso
# de próximo paso, teniendo 20 observaciones.

from app.services.sensors import BACKBONE_KEY, REGISTRY, normalize_sensor_key  # noqa: E402


def test_collection_id_maps_to_the_registry_key():
    assert normalize_sensor_key("sentinel-1-rtc") == "s1"
    assert normalize_sensor_key("sentinel-2-l2a") == "s2"


def test_registry_keys_pass_through():
    for key in REGISTRY:
        assert normalize_sensor_key(key) == key


def test_missing_sensor_defaults_to_the_backbone():
    """Filas del pipeline original, anteriores al campo `sensor`: eran Sentinel-2."""
    assert normalize_sensor_key(None) == BACKBONE_KEY
    assert normalize_sensor_key("") == BACKBONE_KEY


def test_unknown_value_is_returned_untouched():
    """Un sensor que todavía no está en el registro no se disfraza de Sentinel-2:
    se devuelve tal cual para que se note que falta darlo de alta."""
    assert normalize_sensor_key("planetscope") == "planetscope"


def test_every_normalized_key_resolves_in_the_registry():
    for collection in (s.collection for s in REGISTRY.values()):
        assert normalize_sensor_key(collection) in REGISTRY


# ── Resolución vs. tamaño del lote ──
# MODIS a 250 m cubre un lote de 19 ha en 3 píxeles. Eso no es un mapa.

from app.services.sensors import (  # noqa: E402
    LANDSAT,
    MODIS,
    SENTINEL2,
    pixels_for_field,
    useful_for_field,
)


def test_pixel_count_matches_the_geometry():
    # 19.1 ha = 191 000 m². A 10 m/px son 100 m² por píxel.
    assert round(pixels_for_field(SENTINEL2, 19.1)) == 1910
    assert round(pixels_for_field(LANDSAT, 19.1)) == 212
    assert round(pixels_for_field(MODIS, 19.1)) == 3


def test_modis_is_skipped_on_a_small_field():
    assert useful_for_field(SENTINEL2, 19.1)
    assert useful_for_field(LANDSAT, 19.1)
    assert not useful_for_field(MODIS, 19.1)


def test_modis_is_kept_on_a_large_ranch():
    """La regla es por tamaño, no por sensor: con superficie suficiente MODIS aporta."""
    assert useful_for_field(MODIS, 600.0)


def test_unknown_area_does_not_discard_data():
    assert useful_for_field(MODIS, None)


def test_zero_area_yields_no_pixels():
    assert pixels_for_field(MODIS, 0) == 0.0
    assert not useful_for_field(MODIS, 0)


def test_a_sensor_too_coarse_for_the_field_is_not_announced():
    """`next_passes` no debe prometer una imagen que la ingesta ya no va a traer:
    MODIS quedó excluido en lotes chicos, así que anunciar su próxima pasada sería
    esperar algo que nunca llega. (La regla vive en `useful_for_field`; esto fija
    que ambos lados usen el mismo criterio.)"""
    assert not useful_for_field(MODIS, 19.1)
    assert useful_for_field(SENTINEL2, 19.1)
