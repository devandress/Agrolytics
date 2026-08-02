"""Cuándo vuelve a pasar cada satélite sobre una parcela.

El productor no necesita saber de órbitas: necesita saber si vale la pena esperar el
dato de mañana o salir a caminar el lote hoy. Eso se contesta con la fecha del último
paso de cada sensor y su ciclo de repetición, ambos ya en el sistema
(``indices.date`` y ``Sensor.revisit_days``) — sin depender de ningún servicio
externo.

Deliberadamente NO se promete una hora. Para eso hace falta propagar la órbita real
(TLE de Celestrak + un propagador tipo SGP4), y una hora inventada a partir de un
promedio sería exactamente el tipo de falsa precisión que el resto del producto evita.
Cuando se agregue el TLE, este módulo es el lugar donde entra.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.field import Field
from app.models.index import Index
from app.services.sensors import REGISTRY, normalize_sensor_key, useful_for_field


def _sensor_of(meta: dict | None) -> str:
    return normalize_sensor_key((meta or {}).get("sensor"))


def next_pass(last_seen: date, revisit_days: int, today: date | None = None) -> date:
    """Próximo paso esperado: el último visto más un ciclo completo, adelantado hasta
    hoy si el satélite ya pasó varias veces sin que llegara la imagen (nubes, cola de
    procesamiento). Nunca devuelve una fecha pasada."""
    today = today or date.today()
    nxt = last_seen + timedelta(days=revisit_days)
    while nxt < today:
        nxt += timedelta(days=revisit_days)
    return nxt


async def next_passes(db: AsyncSession, field_id: uuid.UUID, today: date | None = None) -> list[dict]:
    """Un renglón por sensor con datos en esta parcela, ordenado por cercanía.

    ``days_ahead`` es lo que el usuario lee de verdad: "en 2 días" pesa más que una
    fecha. ``stale_passes`` cuenta los pasos que ocurrieron sin imagen — si es alto,
    el problema no es la órbita sino la nubosidad, y decirlo evita que el usuario
    piense que el sistema dejó de funcionar.
    """
    today = today or date.today()
    rows = (await db.execute(
        select(Index.date, Index.extra_meta)
        .where(Index.field_id == field_id)
        .order_by(Index.date.desc())
        .limit(400)
    )).all()
    area_ha = (await db.execute(select(Field.area_ha).where(Field.id == field_id))).scalar_one_or_none()

    last_by_sensor: dict[str, date] = {}
    for d, meta in rows:
        sk = _sensor_of(meta)
        if sk not in last_by_sensor or d > last_by_sensor[sk]:
            last_by_sensor[sk] = d

    out: list[dict] = []
    for sk, last in last_by_sensor.items():
        sensor = REGISTRY.get(sk)
        if not sensor or not sensor.enabled:
            continue
        # Un sensor demasiado grueso para este lote ya no se ingiere, así que
        # anunciar su próxima pasada sería prometer una imagen que nunca va a
        # llegar. Puede haber observaciones históricas suyas de antes de la regla.
        if not useful_for_field(sensor, area_ha):
            continue
        nxt = next_pass(last, sensor.revisit_days, today)
        elapsed = (today - last).days
        out.append({
            "sensor": sk,
            "label": sensor.label,
            "res_m": sensor.native_res_m,
            "revisit_days": sensor.revisit_days,
            "last_seen": str(last),
            "days_since": elapsed,
            "next_date": str(nxt),
            "days_ahead": (nxt - today).days,
            # Pasos que deberían haber dejado imagen y no la dejaron.
            "stale_passes": max(0, elapsed // sensor.revisit_days),
        })
    out.sort(key=lambda r: (r["days_ahead"], r["res_m"]))
    return out
