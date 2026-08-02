"""Active Learning Fase 0 — photo-priority active-sampling (docs/ACTIVE_LEARNING.md §1).

Two layers:
  - `photo_priority_for` is pure and deterministic (bar the intentional 20% random
    sampling of "bajo" cases) — covered as plain unit tests, no DB.
  - `generate_pest_pins_for_field` wires that into a real field: verified against a
    real Postgres/PostGIS row, but with `build_pest_risk_pins`/`fetch_pest_weather`
    mocked at the service boundary so this never touches Open-Meteo or rasterio
    fixtures over the network.
"""

import json
import uuid
from unittest.mock import AsyncMock

from geoalchemy2.functions import ST_GeomFromGeoJSON

from app.db.session import AsyncSessionLocal
from app.models.field import Field
from app.models.user import User
from app.services import task_generator
from app.services.task_generator import generate_pest_pins_for_field, photo_priority_for

_POLY = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [-121.5000, 36.6000],
                [-121.5000, 36.6010],
                [-121.4990, 36.6010],
                [-121.4990, 36.6000],
                [-121.5000, 36.6000],
            ]
        ],
    }
)


# ── photo_priority_for: pure unit tests, no DB ──────────────────────────────


def test_priority_alto_alta_confidence_is_low():
    # The rule model is already sure — a photo only audits, doesn't inform much.
    assert photo_priority_for("alto", "alta") == "baja"


def test_priority_alto_low_confidence_is_high():
    # High score resting on little real data — cheap and informative to validate.
    assert photo_priority_for("alto", "baja") == "alta"


def test_priority_alto_media_confidence_is_high():
    assert photo_priority_for("alto", "media") == "alta"


def test_priority_medio_is_always_high_regardless_of_confidence():
    # "medio" is the decision zone — max information per photo, confidence doesn't matter.
    assert photo_priority_for("medio", "alta") == "alta"
    assert photo_priority_for("medio", "baja") == "alta"
    assert photo_priority_for("medio", "media") == "alta"


def test_priority_bajo_is_sampled_high_below_the_20pct_random_cut(monkeypatch):
    # Deterministic via monkeypatch — this pins the *mechanism* (a fixed 20% random
    # sample of "bajo" cases, so the dataset gets clean negatives), not a flaky
    # statistical average over many draws.
    monkeypatch.setattr(task_generator.random, "random", lambda: 0.19)
    assert photo_priority_for("bajo", "alta") == "alta"


def test_priority_bajo_is_media_above_the_20pct_random_cut(monkeypatch):
    monkeypatch.setattr(task_generator.random, "random", lambda: 0.20)
    assert photo_priority_for("bajo", "alta") == "media"


# ── generate_pest_pins_for_field: integration against a real Field row ─────


async def _make_field(crop_type: str = "Lechuga") -> Field:
    async with AsyncSessionLocal() as db:
        user = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x")
        db.add(user)
        await db.flush()
        field = Field(
            user_id=user.id,
            name="Lote de prueba",
            geometry=ST_GeomFromGeoJSON(_POLY),
            crop_type=crop_type,
        )
        db.add(field)
        await db.commit()
        await db.refresh(field)
        return field


async def test_no_pins_skips_the_weather_call_entirely(monkeypatch):
    """No hotspots on the raster → no reason to spend a weather call computing a
    photo_priority nobody will see. Also the cheapest possible offline check that
    build_pest_risk_pins gates fetch_pest_weather."""
    monkeypatch.setattr(task_generator, "build_pest_risk_pins", AsyncMock(return_value=[]))
    fetch_mock = AsyncMock(return_value={})
    monkeypatch.setattr(task_generator, "fetch_pest_weather", fetch_mock)

    field = await _make_field()
    async with AsyncSessionLocal() as db:
        created = await generate_pest_pins_for_field(db, field)

    assert created == []
    fetch_mock.assert_not_called()


async def test_weather_failure_does_not_break_task_generation(monkeypatch):
    """fetch_pest_weather returning {} (Open-Meteo down/timeout, see pest_weather.py's
    own broad except) must not raise — pins still get created, with confidence simply
    degrading to "baja" because assess_pest saw no real measurements."""
    points = [
        {"lat": 36.60050, "lon": -121.49950, "risk": 85.0, "level": "alta", "pest": "Mildiú velloso"}
    ]
    monkeypatch.setattr(task_generator, "build_pest_risk_pins", AsyncMock(return_value=points))
    monkeypatch.setattr(task_generator, "fetch_pest_weather", AsyncMock(return_value={}))
    # Pin the "bajo"/"baja" 20% random branch so the assertion below isn't flaky —
    # the behaviour under test is "doesn't crash / degrades gracefully", not the
    # exact coin flip.
    monkeypatch.setattr(task_generator.random, "random", lambda: 0.99)

    field = await _make_field()
    async with AsyncSessionLocal() as db:
        created = await generate_pest_pins_for_field(db, field)

    assert len(created) == 1
    task = created[0]
    assert task.title == "Riesgo alto de Mildiú velloso"
    # score=0 from all-None weather inputs -> level "bajo", confidence "baja" ->
    # photo_priority_for's else-branch -> "media" at random()=0.99.
    assert task.photo_priority == "media"
