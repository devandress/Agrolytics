"""Seed a self-contained demonstration dataset into an empty deployment.

A fresh Render free-tier deploy has nothing to show: no Celery worker runs there
(see ``render.yaml``), so satellite ingestion never fires and every chart in the
frontend renders empty. This module fills that gap with one account, a handful of
fields, a ~400-day spectral history and a task backlog.

The spectral values are SYNTHETIC. They are generated from the project's own
phenology model (``app.services.phenology``) so the curves agree with what the
phenology, pillars and task endpoints expect to see — but they are not
measurements, and nothing here should be presented as observed data.

Run it by setting ``SEED_DEMO=true``; ``entrypoint.sh`` invokes it after
migrations. ``DEMO_PASSWORD`` is required — the seed refuses to invent one so
that no deployment ends up reachable with a credential committed to the repo.

Idempotent: re-running tops up what is missing and never duplicates rows. The
raster files are rewritten on every run, because the free tier's filesystem is
ephemeral and loses ``DATA_DIR`` on each deploy or restart while the database
rows pointing at it survive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import rasterio
from geoalchemy2.functions import ST_Area, ST_GeomFromGeoJSON, ST_Transform
from rasterio.transform import from_bounds
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.index import Index
from app.models.user import User
from app.services.phenology import (
    _CYCLE_DAYS,
    _DEFAULT_CYCLE,
    expected_ndvi,
    is_vine,
    vine_progress,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────────
HISTORY_DAYS = 400        # the frontend's longest timeseries request is days=400
REVISIT_DAYS = 5          # Sentinel-2 effective revisit with both satellites
RASTER_DATES = 12         # most recent acquisitions that also get a raster on disk
RASTER_PX = 64            # grid side; ~10 m pixels over these fields, like Sentinel-2
INDEX_TYPES = ("NDVI", "NDMI", "NDRE", "EVI")
CLOUD_SKIP_RATE = 0.18    # fraction of revisits dropped, mimicking cloud cover

# Salinas Valley, California — coastal vegetable ground plus a block of wine
# grapes. Matches the crops in ``app.services.anomaly.CROP_THRESHOLDS`` and the
# SGMA framing the role views already use.
DEMO_FIELDS: list[dict] = [
    {
        "name": "Lote Norte — Lechuga",
        "crop_type": "Lechuga",
        "center": (-121.5020, 36.6080),
        "size_deg": (0.0055, 0.0037),
        "planting_offset": -38,   # days before today
        "stress": None,
        "notes": "Riego por goteo, dos líneas por cama. Suelo franco-arcilloso.",
        "context": {
            "riego": "Goteo, 3 riegos por semana",
            "agua": "Pozo compartido, medidor SGMA instalado",
            "fertilizacion": "Fertirriego semanal, N base 120 kg/ha",
            "riesgos": "Mildiú en niebla matinal persistente",
        },
    },
    {
        "name": "Lote Río — Fresa",
        "crop_type": "Fresa",
        "center": (-121.4880, 36.5975),
        "size_deg": (0.0041, 0.0029),
        "planting_offset": -96,
        "stress": (0.62, 0.78),   # visible water-stress episode, as cycle fraction
        "notes": "Macrotúnel parcial. Sector sur con drenaje deficiente.",
        "context": {
            "riego": "Goteo bajo acolchado plástico",
            "agua": "Concesión superficial + pozo de respaldo",
            "fertilizacion": "Fertirriego continuo, bajo N",
            "riesgos": "Araña roja en sector sur; Botrytis tras lluvia",
        },
    },
    {
        "name": "Lote Alto — Brócoli",
        "crop_type": "Brócoli",
        "center": (-121.5185, 36.6135),
        "size_deg": (0.0066, 0.0044),
        "planting_offset": -61,
        "stress": None,
        "notes": "Siembra directa. Rotación tras lechuga.",
        "context": {
            "riego": "Aspersión para establecimiento, luego goteo",
            "agua": "Pozo propio",
            "fertilizacion": "N base 180 kg/ha en dos aplicaciones",
            "riesgos": "Pulgón ceniciento en cabeza",
        },
    },
    {
        "name": "Viñedo Ladera — Vid",
        "crop_type": "Vid",
        "center": (-121.5310, 36.5880),
        "size_deg": (0.0070, 0.0050),
        "planting_offset": None,  # perennial: driven by the calendar season
        "stress": None,
        "notes": "Cabernet en espaldera, marco 2.5 x 1.2 m. Riego deficitario controlado.",
        "context": {
            "riego": "RDI — déficit controlado desde envero",
            "agua": "Pozo con asignación SGMA",
            "fertilizacion": "Aporte foliar, N mínimo",
            "riesgos": "Oídio en racimo; estrés hídrico en ladera oeste",
        },
    },
]

# Ratio of each index to the field's NDVI on the same date. Chosen so every layer
# lands inside its own display range in ``raster_renderer.INDEX_SCALE`` rather
# than saturating at the green end of the ramp, which renders as a flat rectangle.
INDEX_RATIO = {"NDVI": 1.00, "NDMI": 0.45, "NDRE": 0.62, "EVI": 0.92}

# Canopy moisture drops before greenness does — that lead time is the whole point
# of watching NDMI, so the stress episode starts earlier and cuts deeper there.
STRESS_LEAD = 0.08        # cycle fractions by which NDMI leads NDVI
STRESS_DEPTH = {"NDVI": 0.28, "NDMI": 0.45, "NDRE": 0.33, "EVI": 0.30}


def _polygon(center: tuple[float, float], size: tuple[float, float]) -> dict:
    """Axis-aligned rectangle as a GeoJSON polygon, slightly skewed to look surveyed."""
    lon, lat = center
    dx, dy = size[0] / 2, size[1] / 2
    skew = dx * 0.12
    ring = [
        [lon - dx, lat - dy],
        [lon + dx, lat - dy + skew],
        [lon + dx - skew, lat + dy],
        [lon - dx, lat + dy - skew],
        [lon - dx, lat - dy],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _cycle_fraction(spec: dict, day: date) -> float | None:
    """Where *day* falls in the crop's cycle, 0..1, or None when nothing is growing."""
    if is_vine(spec["crop_type"]):
        return vine_progress(day)
    cycle = _CYCLE_DAYS.get(spec["crop_type"], _DEFAULT_CYCLE)
    planted = date.today() + timedelta(days=spec["planting_offset"])
    fallow = 25  # bare ground between successive plantings
    elapsed = (day - planted).days % (cycle + fallow)
    if elapsed < 0 or elapsed > cycle:
        return None
    return elapsed / cycle


def _index_mean(spec: dict, day: date, index_type: str, rng: random.Random) -> float:
    """Plausible field-mean value: the phenology model's expectation plus noise."""
    ratio = INDEX_RATIO[index_type]
    pct = _cycle_fraction(spec, day)
    if pct is None:
        # Bare soil or dormant vine — low, flat, slightly noisy.
        return round(max(0.03, (0.14 + rng.gauss(0, 0.02)) * ratio), 4)

    value = expected_ndvi(spec["crop_type"], pct) * ratio
    stress = spec.get("stress")
    if stress:
        lo, hi = stress
        if index_type == "NDMI":
            lo, hi = lo - STRESS_LEAD, hi - STRESS_LEAD
        if lo <= pct <= hi:
            # Deepest in the middle of the episode, tapering at both ends.
            depth = 1 - abs((pct - (lo + hi) / 2) / ((hi - lo) / 2))
            value *= 1 - STRESS_DEPTH[index_type] * depth
    return round(max(0.03, min(0.95, value + rng.gauss(0, 0.022) * ratio)), 4)


def _acquisition_dates(rng: random.Random) -> list[date]:
    """Revisit dates over the history window, with cloudy passes dropped."""
    today = date.today()
    days = [today - timedelta(days=n) for n in range(0, HISTORY_DAYS, REVISIT_DAYS)]
    return sorted(d for d in days if rng.random() > CLOUD_SKIP_RATE)


def _write_raster(path: Path, mean: float, bounds: tuple[float, float, float, float],
                  rng: random.Random) -> str:
    """Write a small float32 GeoTIFF whose pixels vary around *mean*.

    Structure matters more than realism here: a gradient plus a couple of weaker
    patches gives the map overlay something with shape, so the colormap does not
    render as a flat rectangle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ys, xs = np.mgrid[0:RASTER_PX, 0:RASTER_PX] / RASTER_PX

    # Base gradient: one corner drier than the other.
    angle = rng.uniform(0, 2 * np.pi)
    grid = np.cos(angle) * xs + np.sin(angle) * ys
    grid = (grid - grid.min()) / (np.ptp(grid) or 1) - 0.5

    # Two soft depressions — the "problem patches" a scout would walk to.
    for _ in range(2):
        cx, cy = rng.uniform(0.15, 0.85), rng.uniform(0.15, 0.85)
        r = rng.uniform(0.10, 0.22)
        grid -= rng.uniform(0.4, 0.9) * np.exp(-(((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * r ** 2)))

    grid = grid / (np.abs(grid).max() or 1)
    # ±18% around the field mean — in-field variation a scout would recognise,
    # without the whole rectangle sweeping the colour ramp end to end.
    data = (mean + grid * mean * 0.18).astype("float32")
    data = np.clip(data, 0.02, 0.98).astype("float32")

    profile = {
        "driver": "GTiff", "dtype": "float32", "count": 1,
        "height": RASTER_PX, "width": RASTER_PX,
        "crs": "EPSG:4326", "transform": from_bounds(*bounds, RASTER_PX, RASTER_PX),
        "tiled": True, "compress": "deflate", "blockxsize": 256, "blockysize": 256,
    }
    with rasterio.open(str(path), "w", **profile) as dst:
        dst.write(data, 1)
    return str(path)


def _bounds_of(geo: dict) -> tuple[float, float, float, float]:
    ring = geo["coordinates"][0]
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


DEMO_TASKS: list[dict] = [
    {"field": 1, "task_type": "riego", "title": "Regar sector sur",
     "detail": "NDMI por debajo del umbral tres pasadas seguidas en el tercio sur.",
     "priority": 1, "recommended_value": "22 mm", "zone": "sur", "due_in": 0},
    {"field": 1, "task_type": "inspeccion", "title": "Revisar araña roja",
     "detail": "Caída de NDVI localizada compatible con ácaro. Confirmar en campo.",
     "priority": 2, "recommended_value": None, "zone": "sur", "due_in": 1},
    {"field": 0, "task_type": "fertilizacion", "title": "Aplicar nitrógeno",
     "detail": "NDRE estable pero por debajo del esperado para el estado fenológico.",
     "priority": 2, "recommended_value": "45 kg N/ha", "zone": "completo", "due_in": 2},
    {"field": 2, "task_type": "inspeccion", "title": "Monitorear pulgón",
     "detail": "Ventana de riesgo por temperatura acumulada.",
     "priority": 3, "recommended_value": None, "zone": "norte", "due_in": 3},
    {"field": 3, "task_type": "riego", "title": "Ajustar déficit controlado",
     "detail": "Envero en curso; sostener NDMI cerca del umbral sin cruzarlo.",
     "priority": 2, "recommended_value": "12 mm", "zone": "ladera oeste", "due_in": 1},
    {"field": 0, "task_type": "riego", "title": "Riego de establecimiento",
     "detail": "Completado tras el trasplante.", "priority": 3,
     "recommended_value": "18 mm", "zone": "completo", "due_in": -6, "done": True},
    {"field": 2, "task_type": "fertilizacion", "title": "Primera aplicación de N",
     "detail": "Dosis base aplicada en banda.", "priority": 3,
     "recommended_value": "90 kg N/ha", "zone": "completo", "due_in": -11, "done": True},
]


async def _ensure_user(session) -> User:
    email = os.getenv("DEMO_EMAIL", "demo@agrolytics.app").strip().lower()
    password = os.getenv("DEMO_PASSWORD", "").strip()
    plan = os.getenv("DEMO_PLAN", "pro").strip()

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user:
        if user.plan != plan:
            user.plan = plan
            await session.commit()
            logger.info("Demo user plan updated to %s", plan)
        logger.info("Demo user already exists: %s", email)
        return user

    if not password:
        raise SystemExit(
            "DEMO_PASSWORD is not set. Refusing to create the demo account with a "
            "default password — set DEMO_PASSWORD in the environment and re-run."
        )
    if len(password) < 12:
        raise SystemExit("DEMO_PASSWORD must be at least 12 characters.")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        role="farmer",
        full_name=os.getenv("DEMO_FULL_NAME", "Rancho San Miguel"),
        plan=plan,
        preferences={"language": "es", "units": "metric", "timezone": "America/Los_Angeles"},
        notifications={"riego": True, "plagas": True, "email": False, "in_app": True},
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("Demo user created: %s (plan=%s)", email, plan)
    return user


async def _ensure_fields(session, user: User) -> list[Field]:
    fields: list[Field] = []
    for spec in DEMO_FIELDS:
        existing = (await session.execute(
            select(Field).where(Field.user_id == user.id, Field.name == spec["name"])
        )).scalar_one_or_none()
        if existing:
            fields.append(existing)
            continue

        geo = _polygon(spec["center"], spec["size_deg"])
        geojson_str = json.dumps(geo)
        area_ha = (await session.execute(
            select(ST_Area(ST_Transform(ST_GeomFromGeoJSON(geojson_str), 3857)) / 10_000)
        )).scalar_one() or 0.0

        planted = (date.today() + timedelta(days=spec["planting_offset"])
                   if spec["planting_offset"] is not None else None)
        cycle = _CYCLE_DAYS.get(spec["crop_type"], _DEFAULT_CYCLE)
        field = Field(
            user_id=user.id,
            name=spec["name"],
            geometry=ST_GeomFromGeoJSON(geojson_str),
            area_ha=round(area_ha, 4),
            crop_type=spec["crop_type"],
            notes=spec["notes"],
            planting_date=planted,
            expected_harvest=(planted + timedelta(days=cycle)) if planted else None,
            context=spec["context"],
        )
        session.add(field)
        await session.commit()
        await session.refresh(field)
        logger.info("Field created: %s (%.1f ha)", field.name, field.area_ha)
        fields.append(field)
    return fields


async def _ensure_indices(session, fields: list[Field]) -> None:
    """Insert missing index rows and (re)write the raster files they point at."""
    for spec, field in zip(DEMO_FIELDS, fields, strict=True):
        # Deterministic per field, so re-runs and redeploys reproduce the same story.
        rng = random.Random(f"agrolytics-demo::{field.name}")
        dates = _acquisition_dates(rng)
        raster_dates = set(dates[-RASTER_DATES:])
        bounds = _bounds_of(_polygon(spec["center"], spec["size_deg"]))
        out_dir = Path(settings.DATA_DIR) / "cog" / str(field.id)

        existing = set((await session.execute(
            select(Index.date, Index.index_type).where(Index.field_id == field.id)
        )).all())

        new_rows = 0
        for acq in dates:
            for index_type in INDEX_TYPES:
                mean = _index_mean(spec, acq, index_type, rng)
                uri = None
                if acq in raster_dates:
                    path = out_dir / f"{index_type}_{acq.isoformat().replace('-', '')}.tif"
                    # Always rewrite: DATA_DIR is ephemeral on the free tier, so a
                    # row can outlive the file it references.
                    uri = _write_raster(path, mean, bounds,
                                        random.Random(f"{field.name}{acq}{index_type}"))
                if (acq, index_type) in existing:
                    continue
                session.add(Index(
                    field_id=field.id, date=acq, index_type=index_type,
                    raster_uri=uri, mean_value=mean,
                    extra_meta={"sensor": "s2", "native_res_m": 10,
                                "scene_id": f"DEMO_S2_{acq.isoformat().replace('-', '')}"},
                ))
                new_rows += 1
        await session.commit()
        logger.info("Field %s: %d index rows added, %d dates with rasters",
                    field.name, new_rows, len(raster_dates))


async def _ensure_tasks(session, fields: list[Field], user: User) -> None:
    for spec in DEMO_TASKS:
        field = fields[spec["field"]]
        existing = (await session.execute(
            select(FieldTask.id).where(
                FieldTask.field_id == field.id, FieldTask.title == spec["title"]
            )
        )).scalar_one_or_none()
        if existing:
            continue
        done = spec.get("done", False)
        session.add(FieldTask(
            field_id=field.id,
            task_type=spec["task_type"],
            title=spec["title"],
            detail=spec["detail"],
            priority=spec["priority"],
            status="hecho" if done else "pendiente",
            recommended_value=spec["recommended_value"],
            zone=spec["zone"],
            due_date=date.today() + timedelta(days=spec["due_in"]),
            completed_at=datetime.now(UTC) if done else None,
            completed_by=user.id if done else None,
        ))
    await session.commit()
    logger.info("Demo tasks ensured (%d defined)", len(DEMO_TASKS))


async def seed_demo() -> None:
    async with AsyncSessionLocal() as session:
        user = await _ensure_user(session)
        fields = await _ensure_fields(session, user)
        await _ensure_indices(session, fields)
        await _ensure_tasks(session, fields, user)
    logger.info("Demo dataset ready.")


if __name__ == "__main__":
    asyncio.run(seed_demo())
