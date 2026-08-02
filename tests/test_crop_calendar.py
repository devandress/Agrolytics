"""Calendario real del Valle de Mexicali.

Lo que se prueba acá no es aritmética: es que el módulo no invente. Las superficies
son las de los boletines oficiales y el mes de cosecha no está publicado, así que
tiene que seguir ausente.
"""

from datetime import date

from app.services import crop_calendar as cc


def test_lettuce_hectares_match_the_official_bulletins():
    lechuga = cc.find("Lechuga")
    assert lechuga is not None
    assert lechuga.hectares_2019_20 == 804.0
    assert lechuga.hectares_2020_21 == 1897.0


def test_harvest_month_stays_unknown():
    """Ninguna fuente consultada publica mes de cosecha por cultivo. Si alguien lo
    rellena, que sea con una fuente, no con una estimación."""
    assert all(c.harvest_month is None for c in cc.MEXICALI_OI)


def test_unknown_crop_gets_no_invented_date():
    assert cc.find("Aguacate") is None
    assert cc.typical_planting_date("Aguacate") is None


def test_planting_date_uses_the_cycle_already_in_the_ground():
    """En enero el ciclo vigente arrancó en octubre del año anterior: la siembra fue
    en noviembre de ESE año, no del actual."""
    assert cc.typical_planting_date("Lechuga", today=date(2026, 1, 15)) == date(2025, 11, 1)


def test_planting_date_after_october_uses_the_new_cycle():
    assert cc.typical_planting_date("Lechuga", today=date(2026, 11, 20)) == date(2026, 11, 1)


def test_lookup_is_case_and_space_insensitive():
    assert cc.find("  lechuga ") is cc.find("Lechuga")
