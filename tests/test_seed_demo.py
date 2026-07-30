"""Unit tests for the demonstration seed's pure helpers.

Only the value/geometry generators are covered here — they need no database, and
they are what determines whether the seeded dataset looks agronomically sane.
"""

from datetime import date, timedelta

import pytest

from app.seed_demo import (
    DEMO_FIELDS,
    INDEX_RATIO,
    INDEX_TYPES,
    _cycle_fraction,
    _index_mean,
    _polygon,
)

FRESA = next(f for f in DEMO_FIELDS if f["crop_type"] == "Fresa")
VID = next(f for f in DEMO_FIELDS if f["crop_type"] == "Vid")


def _rng():
    import random
    return random.Random(0)


def test_polygon_is_closed_ring():
    geo = _polygon((-121.5, 36.6), (0.004, 0.003))
    ring = geo["coordinates"][0]
    assert geo["type"] == "Polygon"
    assert ring[0] == ring[-1], "GeoJSON ring must close on itself"
    assert len(ring) == 5


def test_polygon_spans_requested_size():
    geo = _polygon((-121.5, 36.6), (0.004, 0.003))
    lons = [p[0] for p in geo["coordinates"][0]]
    lats = [p[1] for p in geo["coordinates"][0]]
    assert max(lons) - min(lons) == pytest.approx(0.004, abs=1e-9)
    assert max(lats) - min(lats) == pytest.approx(0.003, abs=1e-9)


@pytest.mark.parametrize("index_type", INDEX_TYPES)
def test_index_means_stay_in_range(index_type):
    """Every generated value must be a plausible index reading, never out of band."""
    rng = _rng()
    for offset in range(0, 400, 5):
        day = date.today() - timedelta(days=offset)
        value = _index_mean(FRESA, day, index_type, rng)
        assert 0.0 < value < 1.0


def test_index_ordering_matches_ratios():
    """NDVI is the highest layer and NDMI the lowest, as the ratios intend."""
    day = date.today()
    values = {t: _index_mean(FRESA, day, t, _rng()) for t in INDEX_TYPES}
    assert values["NDVI"] == max(values.values())
    assert values["NDMI"] == min(values.values())
    assert INDEX_RATIO["NDMI"] < INDEX_RATIO["NDRE"] < INDEX_RATIO["EVI"]


def test_vine_is_dormant_out_of_season():
    """Vines have no cycle fraction in deep winter, so they read as bare ground."""
    winter = date(date.today().year, 1, 15)
    assert _cycle_fraction(VID, winter) is None
    value = _index_mean(VID, winter, "NDVI", _rng())
    assert value < 0.25, "dormant vine should not look like a closed canopy"


def test_annual_crop_cycles_between_bare_and_canopy():
    """An annual's NDVI must both bottom out (fallow) and peak (canopy) over 400 days."""
    rng = _rng()
    values = [
        _index_mean(FRESA, date.today() - timedelta(days=n), "NDVI", rng)
        for n in range(0, 400, 5)
    ]
    assert min(values) < 0.25, "no fallow period generated"
    assert max(values) > 0.60, "canopy never closes"


def test_moisture_stress_leads_greenness():
    """NDMI must dip before NDVI does — the lead time the product is sold on.

    Compared per cycle fraction against each index's own unstressed expectation;
    the global minimum is useless here because it falls in the fallow gap, where
    both indices bottom out together.
    """
    from app.services.phenology import expected_ndvi

    assert FRESA["stress"] is not None, "this test needs the stressed demo field"
    lo, hi = FRESA["stress"]

    first_dip: dict[str, float] = {}
    for index_type in ("NDVI", "NDMI"):
        for n in range(400, -1, -5):  # walk forward in time
            day = date.today() - timedelta(days=n)
            pct = _cycle_fraction(FRESA, day)
            if pct is None or not (lo - 0.25 <= pct <= hi):
                continue
            unstressed = expected_ndvi(FRESA["crop_type"], pct) * INDEX_RATIO[index_type]
            if _index_mean(FRESA, day, index_type, _rng()) < unstressed * 0.9:
                first_dip.setdefault(index_type, pct)
                break

    assert set(first_dip) == {"NDVI", "NDMI"}, f"no dip detected: {first_dip}"
    assert first_dip["NDMI"] < first_dip["NDVI"], (
        f"NDMI should dip first, got NDMI at {first_dip['NDMI']:.2f} "
        f"vs NDVI at {first_dip['NDVI']:.2f} of the cycle"
    )
