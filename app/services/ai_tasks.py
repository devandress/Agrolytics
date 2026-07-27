"""AI-driven task generation from real satellite data (DeepSeek).

Gathers a field's *real* index observations (NDVI/NDMI/NDRE means + dates + simple
trend), the crop and basic context, and asks DeepSeek to propose 1–4 concrete,
prioritised field tasks. Tasks persist as ``FieldTask`` (same model the map pins and
role views already use). Falls back to the deterministic rule engine
(``task_generator``) when the AI is unavailable or returns nothing usable.

Principle: only generate from observed data. Fields with no real indices produce no
tasks (left blank) — we never invent data that would become noise for a future model.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.index import Index
from app.services.task_generator import _has_open_task, generate_pest_pins_for_field, generate_tasks_for_field

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = "deepseek-chat"
_VALID_TYPES = {"riego", "fertilizacion", "inspeccion", "otro"}

_SYSTEM = """Eres un agrónomo experto en vegetales costeros y en viticultura (uva de vino). Recibes
datos satelitales REALES de una parcela y el CONTEXTO del agricultor (sistema de riego, fertilización,
riesgos, fenología).

Metas del sistema, en orden: (1) reducir el uso de AGUA, (2) reducir FERTILIZANTE, (3) prevenir
SINIESTROS (heladas, plagas, salinidad). Usa el contexto para afinar cada recomendación: p. ej. si
el riego es por goteo recomienda láminas precisas; si el agua es limitada, prioriza eficiencia; si
hay historial de heladas/salinidad, advierte y previene.

VID (uva de vino): aplica riego deficitario controlado (RDI). Un déficit hídrico moderado en
maduración (post-envero) es DESEABLE para la calidad: NO recomiendes regar por un NDMI bajo si la
vid está madurando; sí prioriza agua en brotación/floración. La vid necesita POCO nitrógeno (~40 kg
N/ha; el exceso daña la calidad). Riesgos clave: oídio/mildiú/botrytis, polilla del racimo, helada de
brotación y Pierce's disease (chicharrita).

Genera entre 1 y 4 tareas accionables, concretas y priorizadas, basadas SOLO en los datos y el
contexto. Responde EXCLUSIVAMENTE con un array JSON, sin texto ni markdown. Cada elemento:
{"task_type":"riego|fertilizacion|inspeccion|otro","title":"...","detail":"breve porqué citando el índice y el contexto","priority":1-4,"recommended_value":"ej. 25 mm o 120 kg N/ha o null"}
priority 1 = urgente, 4 = baja. Si los datos están sanos, devuelve [] (sin tareas)."""


async def _gather(db: AsyncSession, field: Field) -> dict | None:
    """Collect a field's latest real index observations + farmer context. None if no real data."""
    data: dict = {"campo": field.name, "cultivo": field.crop_type or "desconocido", "indices": {}}
    # Farmer context + phenology (días desde siembra) to steer the AI toward the goals.
    if field.context:
        data["contexto"] = field.context
    if field.planting_date:
        data["fecha_siembra"] = str(field.planting_date)
        data["dias_desde_siembra"] = (date.today() - field.planting_date).days
    if field.expected_harvest:
        data["cosecha_estimada_aprox"] = str(field.expected_harvest)
    found = False
    for idx in ("NDVI", "NDMI", "NDRE", "EVI"):
        rows = (await db.execute(
            select(Index.date, Index.mean_value, Index.extra_meta)
            .where(Index.field_id == field.id, Index.index_type == idx, Index.mean_value.isnot(None))
            .order_by(Index.date.desc()).limit(4))).all()
        if not rows:
            continue
        found = True
        series = [{"fecha": str(d), "valor": round(v, 3), "sensor": (m or {}).get("sensor", "s2")}
                  for d, v, m in rows]
        latest, oldest = rows[0][1], rows[-1][1]
        data["indices"][idx] = {"ultimo": round(latest, 3),
                                "tendencia": "baja" if latest < oldest - 0.02 else "sube" if latest > oldest + 0.02 else "estable",
                                "serie": series}
    return data if found else None


def _parse_tasks(text: str) -> list[dict]:
    """Extract a JSON task array from the model's reply, tolerating fences/prose."""
    text = text.strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for t in arr if isinstance(arr, list) else []:
        if not isinstance(t, dict):
            continue
        tt = str(t.get("task_type", "otro")).lower()
        if tt not in _VALID_TYPES:
            tt = "otro"
        try:
            prio = int(t.get("priority", 3))
        except (TypeError, ValueError):
            prio = 3
        rv = t.get("recommended_value")
        out.append({
            "task_type": tt,
            "title": str(t.get("title", "Tarea"))[:255],
            "detail": str(t.get("detail", ""))[:500],
            "priority": min(4, max(1, prio)),
            "recommended_value": (str(rv)[:100] if rv not in (None, "null", "") else None),
        })
    return out[:4]


async def _call_deepseek(payload: dict) -> list[dict]:
    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "max_tokens": 700,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(_DEEPSEEK_URL, json=body, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek {resp.status_code}: {resp.text[:200]}")
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_tasks(content)


async def generate_ai_tasks_for_field(db: AsyncSession, field: Field) -> tuple[int, str]:
    """Generate AI tasks for one field from real data. Returns (count, source)."""
    payload = await _gather(db, field)
    if payload is None:
        return 0, "sin_datos"  # no real observations → leave blank

    if settings.DEEPSEEK_API_KEY:
        try:
            tasks = await _call_deepseek(payload)
            created = 0
            for t in tasks:
                if await _has_open_task(db, field.id, t["task_type"]):
                    continue
                db.add(FieldTask(
                    field_id=field.id, task_type=t["task_type"], title=t["title"],
                    detail=t["detail"], priority=t["priority"],
                    recommended_value=t["recommended_value"], due_date=date.today()))
                created += 1
            # Pest pins are independent of the AI/rules choice for the other tasks.
            created += len(await generate_pest_pins_for_field(db, field))
            if created:
                await db.flush()
                logger.info(f"AI generated {created} task(s) for field {field.id}")
            return created, "ia"
        except Exception as exc:
            logger.warning(f"AI task gen failed for {field.id} ({exc}); using rules.")

    # Fallback: deterministic rule engine over the same real data.
    created = await generate_tasks_for_field(db, field)
    return len(created), "reglas"


async def propose_tasks(db: AsyncSession, field: Field, messages: list[dict] | None = None) -> dict:
    """Propose (NOT save) tasks for a field from its real data + optional chat history.

    Returns ``{"tasks": [...], "reply": str}``. The user confirms which to keep via
    the create-one-task endpoint. Honest: empty if there's no real observation data.
    """
    payload = await _gather(db, field)
    if payload is None:
        return {"tasks": [], "reply": "Aún no hay datos satelitales reales de esta parcela, así que no propongo tareas (no inventamos datos)."}
    if messages:
        payload["conversacion"] = messages[-6:]  # últimos turnos como contexto

    # Take existing map points/tasks into account so the AI doesn't duplicate them.
    existing = (await db.execute(
        select(FieldTask.task_type, FieldTask.title, FieldTask.lat, FieldTask.lon)
        .where(FieldTask.field_id == field.id, FieldTask.status == "pendiente"))).all()
    if existing:
        payload["tareas_existentes"] = [
            {"tipo": tt, "titulo": ti, "punto": ([round(la, 5), round(lo, 5)] if la and lo else None)}
            for tt, ti, la, lo in existing]
    if not settings.DEEPSEEK_API_KEY:
        return {"tasks": [], "reply": "El asistente de IA no está configurado."}
    try:
        tasks = await _call_deepseek(payload)
        if not tasks:
            return {"tasks": [], "reply": "Los índices se ven sanos: no propongo tareas por ahora."}
        return {"tasks": tasks, "reply": f"Te propongo {len(tasks)} tarea(s) según los datos. Revísalas y agrega las que quieras."}
    except Exception as exc:
        logger.warning(f"propose_tasks failed for {field.id}: {exc}")
        return {"tasks": [], "reply": "No pude analizar ahora mismo. Intenta de nuevo en unos segundos."}


async def generate_ai_tasks_for_user(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Run AI task generation across all of a user's fields."""
    fields = (await db.execute(select(Field).where(Field.user_id == user_id))).scalars().all()
    total, source = 0, "ia"
    for f in fields:
        n, src = await generate_ai_tasks_for_field(db, f)
        total += n
        if src == "reglas":
            source = "reglas"
    await db.commit()
    return {"created": total, "source": source}
