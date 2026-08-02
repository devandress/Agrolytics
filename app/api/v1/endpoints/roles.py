"""Role-based view endpoints (Dueño · Mayordomo · Regador · Agrónomo · Trabajador).

These are mostly aggregation/presentation over existing data, plus persistence for
field tasks and validation photos. All routes are owner-scoped via CurrentUser.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, CurrentUser, DBSession
from app.core.config import settings
from app.core.demo_data import financial_summary
from app.models.field import Field
from app.models.field_photo import FieldPhoto
from app.models.field_task import FieldTask
from app.models.index import Index
from app.schemas.task import PhotoLabelIn, PhotoOut, PhotoReviewIn, TaskOut, TaskWithFieldOut
from app.services.ai_tasks import generate_ai_tasks_for_user
from app.services.pest_risk import pest_risk
from app.services.ranch_health import ranch_overview
from app.services.task_generator import generate_tasks_for_user
from app.services.uploads import UnsupportedImage, UploadTooLarge, read_image_upload

router = APIRouter()


async def _owned_field(db: AsyncSession, field_id: uuid.UUID, user_id: uuid.UUID) -> Field:
    r = await db.execute(select(Field).where(Field.id == field_id, Field.user_id == user_id))
    field = r.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=404, detail="Campo no encontrado.")
    return field


# ══════════════ DUEÑO ══════════════
@router.get("/ranch/overview")
async def get_ranch_overview(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Owner overview: ranch health score, per-field breakdown, demo finance/SGMA."""
    return await ranch_overview(db, current_user.id)


# ══════════════ MAYORDOMO / TRABAJADOR ══════════════
@router.get("/tasks", response_model=list[TaskWithFieldOut])
async def list_tasks(
    current_user: CurrentUser,
    db: DBSession,
    status_filter: str | None = Query(None, alias="status", description="pendiente | hecho"),
    field_id: uuid.UUID | None = Query(None),
) -> list[TaskWithFieldOut]:
    """Prioritised task list across all of the user's fields."""
    q = (
        select(FieldTask, Field.name, Field.crop_type)
        .join(Field, FieldTask.field_id == Field.id)
        .where(Field.user_id == current_user.id)
    )
    if status_filter:
        q = q.where(FieldTask.status == status_filter)
    if field_id:
        q = q.where(FieldTask.field_id == field_id)
    q = q.order_by(FieldTask.status.asc(), FieldTask.priority.asc(), FieldTask.due_date.asc())

    rows = (await db.execute(q)).all()
    out: list[TaskWithFieldOut] = []
    for task, fname, crop in rows:
        item = TaskWithFieldOut.model_validate(task)
        item.field_name = fname
        item.crop_type = crop
        out.append(item)
    return out


@router.post("/tasks/generate")
async def generate_tasks(
    current_user: CurrentUser, db: DBSession, use_ai: bool = Query(True)
) -> dict[str, Any]:
    """(Re)generate tasks from the latest *real* indices.

    With ``use_ai`` (default) DeepSeek proposes tasks from the observed data and
    falls back to the rule engine if the AI is unavailable. Fields without real
    observations are left blank (no synthetic tasks).
    """
    if use_ai:
        res = await generate_ai_tasks_for_user(db, current_user.id)
        src = "IA" if res["source"] == "ia" else "reglas"
        return {
            "created": res["created"],
            "source": res["source"],
            "tasks": res["tasks"],
            "message": f"Se generaron {res['created']} tarea(s) ({src}).",
        }
    created = await generate_tasks_for_user(db, current_user.id)
    # The deterministic-rules-only path doesn't track per-task photo_priority detail
    # (generate_tasks_for_user only returns a count) — kept as an empty list for
    # response-shape compatibility with the use_ai=True path.
    return {
        "created": created,
        "source": "reglas",
        "tasks": [],
        "message": f"Se generaron {created} tarea(s) (reglas).",
    }


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
async def complete_task(task_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> TaskOut:
    """Mark a task as done."""
    r = await db.execute(
        select(FieldTask)
        .join(Field, FieldTask.field_id == Field.id)
        .where(FieldTask.id == task_id, Field.user_id == current_user.id)
    )
    task = r.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    task.status = "hecho"
    task.completed_at = datetime.now(UTC)
    task.completed_by = current_user.id
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


# ── El productor decide sobre lo que el sistema propuso ──
# El satélite ve síntomas, no el campo. Quien camina el lote sabe que esa mancha es
# la sombra de un árbol, o que ese sector ya se regó ayer. Por eso el sistema
# propone y él dispone — y cuando dice que no, ese "no" se guarda.


class RejectBody(BaseModel):
    reason: str | None = None


async def _owned_task(db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID) -> FieldTask:
    r = await db.execute(
        select(FieldTask)
        .join(Field, FieldTask.field_id == Field.id)
        .where(FieldTask.id == task_id, Field.user_id == user_id)
    )
    task = r.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return task


@router.get("/tasks/proposals", response_model=list[TaskWithFieldOut])
async def list_proposals(current_user: CurrentUser, db: DBSession) -> list[TaskWithFieldOut]:
    """Lo que el sistema propuso y todavía nadie decidió, más urgente primero."""
    rows = (
        await db.execute(
            select(FieldTask, Field.name, Field.crop_type)
            .join(Field, FieldTask.field_id == Field.id)
            .where(Field.user_id == current_user.id, FieldTask.status == "propuesta")
            .order_by(FieldTask.priority.asc(), FieldTask.due_date.asc())
        )
    ).all()
    out = []
    for task, field_name, crop in rows:
        item = TaskWithFieldOut.model_validate(task)
        item.field_name = field_name
        item.crop_type = crop
        out.append(item)
    return out


@router.post("/tasks/{task_id}/approve", response_model=TaskOut)
async def approve_task(task_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> TaskOut:
    """Aceptar una propuesta: pasa a ser una tarea a hacer."""
    task = await _owned_task(db, task_id, current_user.id)
    if task.status != "propuesta":
        raise HTTPException(status_code=409, detail=f"La tarea ya está en estado '{task.status}'.")
    task.status = "pendiente"
    task.decided_at = datetime.now(UTC)
    task.decided_by = current_user.id
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.post("/tasks/{task_id}/reject", response_model=TaskOut)
async def reject_task(
    task_id: uuid.UUID, current_user: CurrentUser, db: DBSession, body: RejectBody | None = None
) -> TaskOut:
    """Descartar una propuesta, opcionalmente diciendo por qué.

    El motivo no es burocracia: es la corrección de alguien que conoce el lote. Que
    rechacen "regar" repetidamente en la misma parcela dice que el umbral está mal
    ahí, y eso no se aprende de ningún satélite.
    """
    task = await _owned_task(db, task_id, current_user.id)
    if task.status != "propuesta":
        raise HTTPException(status_code=409, detail=f"La tarea ya está en estado '{task.status}'.")
    task.status = "descartada"
    task.decided_at = datetime.now(UTC)
    task.decided_by = current_user.id
    if body and body.reason:
        task.rejection_reason = body.reason[:300]
    await db.commit()
    await db.refresh(task)
    return TaskOut.model_validate(task)


@router.post("/tasks/proposals/approve-all")
async def approve_all_proposals(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Aceptar todas las propuestas abiertas de una sola vez.

    Existe porque la mayoría de los días el productor está de acuerdo con todo, y
    obligarlo a tocar diez veces convierte la aprobación en un trámite que va a
    terminar salteándose.
    """
    rows = (
        await db.execute(
            select(FieldTask)
            .join(Field, FieldTask.field_id == Field.id)
            .where(Field.user_id == current_user.id, FieldTask.status == "propuesta")
        )
    ).scalars().all()
    now = datetime.now(UTC)
    for task in rows:
        task.status = "pendiente"
        task.decided_at = now
        task.decided_by = current_user.id
    await db.commit()
    return {"approved": len(rows)}


# ══════════════ REGADOR ══════════════
@router.get("/irrigation/schedule")
async def irrigation_schedule(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Irrigation tasks + per-field moisture (NDMI/RVI) + demo water budget."""
    # Pending irrigation tasks across fields
    rows = (
        await db.execute(
            select(FieldTask, Field.name)
            .join(Field, FieldTask.field_id == Field.id)
            .where(
                Field.user_id == current_user.id,
                FieldTask.task_type == "riego",
                FieldTask.status == "pendiente",
            )
            .order_by(FieldTask.priority.asc())
        )
    ).all()
    schedule = [
        {
            "task_id": str(t.id),
            "field_id": str(t.field_id),
            "field_name": fname,
            "title": t.title,
            "amount": t.recommended_value,
            "priority": t.priority,
            "zone": t.zone,
        }
        for t, fname in rows
    ]

    # Per-field moisture proxy (latest NDMI + radar RVI)
    fields = (await db.execute(select(Field).where(Field.user_id == current_user.id))).scalars().all()
    moisture = []
    for f in fields:
        ndmi = (
            await db.execute(
                select(Index.mean_value)
                .where(Index.field_id == f.id, Index.index_type == "NDMI", Index.mean_value.isnot(None))
                .order_by(Index.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        rvi = (
            await db.execute(
                select(Index.mean_value)
                .where(Index.field_id == f.id, Index.index_type == "RVI", Index.mean_value.isnot(None))
                .order_by(Index.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        # Moisture % proxy from NDMI (-1..1 → 0..100)
        pct = None if ndmi is None else max(0, min(100, round((ndmi + 0.2) / 0.8 * 100)))
        moisture.append(
            {
                "field_id": str(f.id),
                "name": f.name,
                "ndmi": round(ndmi, 3) if ndmi is not None else None,
                "rvi": round(rvi, 3) if rvi is not None else None,
                "moisture_pct": pct,
            }
        )

    return {
        "schedule": schedule,
        "moisture": moisture,
        "water_budget": financial_summary()["sgma"],
        "is_demo": True,
    }


# ══════════════ AGRÓNOMO ══════════════
@router.get("/portfolio")
async def get_portfolio(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Portfolio table: every field with health, open tasks and pest risk (demo)."""
    fields = (await db.execute(select(Field).where(Field.user_id == current_user.id))).scalars().all()
    items = []
    for f in fields:
        ndmi = (
            await db.execute(
                select(Index.mean_value)
                .where(Index.field_id == f.id, Index.index_type == "NDMI", Index.mean_value.isnot(None))
                .order_by(Index.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        ndvi = (
            await db.execute(
                select(Index.mean_value)
                .where(Index.field_id == f.id, Index.index_type == "NDVI", Index.mean_value.isnot(None))
                .order_by(Index.date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        open_tasks = (
            await db.execute(
                select(FieldTask.id).where(FieldTask.field_id == f.id, FieldTask.status == "pendiente")
            )
        ).all()
        items.append(
            {
                "field_id": str(f.id),
                "name": f.name,
                "crop_type": f.crop_type,
                "area_ha": f.area_ha,
                "ndvi": round(ndvi, 3) if ndvi is not None else None,
                "open_tasks": len(open_tasks),
                "pest_risk": pest_risk(f.crop_type, float(ndmi) if ndmi is not None else None),
            }
        )
    return {"fields": items, "total": len(items)}


# ══════════════ DATA PIPELINE (export para cloud) ══════════════
@router.get("/data/bundle")
async def data_bundle(current_user: CurrentUser, db: DBSession) -> dict[str, Any]:
    """Whole-ranch data bundle (all fields' real observations) — ready for cloud fusion.

    Honest: this is the export payload of the pipeline. The actual cloud-side fusion
    job is out of scope here; this endpoint produces the normalised feed it consumes.
    """
    fields = (await db.execute(select(Field).where(Field.user_id == current_user.id))).scalars().all()
    bundle = []
    total = 0
    for f in fields:
        rows = (
            await db.execute(
                select(Index.date, Index.index_type, Index.mean_value, Index.extra_meta).where(
                    Index.field_id == f.id, Index.mean_value.isnot(None)
                )
            )
        ).all()
        obs = [
            {"date": str(d), "index": it, "value": round(v, 5), "sensor": (m or {}).get("sensor", "s2")}
            for d, it, v, m in rows
        ]
        total += len(obs)
        bundle.append(
            {
                "field_id": str(f.id),
                "name": f.name,
                "crop_type": f.crop_type,
                "context": f.context,
                "planting_date": str(f.planting_date) if f.planting_date else None,
                "n_obs": len(obs),
                "observations": obs,
            }
        )
    return {
        "ranch_owner": str(current_user.id),
        "fields": len(fields),
        "total_observations": total,
        "schema_version": "1.0",
        "destination": "cloud-fusion (pendiente)",
        "bundle": bundle,
    }


# ══════════════ FOTOS (validación de campo) ══════════════
@router.post("/fields/{field_id}/photos", response_model=PhotoOut)
async def upload_photo(
    field_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    file: UploadFile = File(...),
    caption: str | None = Form(None),
    alert_confirmed: bool | None = Form(None),
    task_id: uuid.UUID | None = Form(None),
    lat: float | None = Form(None),
    lon: float | None = Form(None),
) -> PhotoOut:
    """Upload a field photo (multipart) that confirms/corrects an alert."""
    await _owned_field(db, field_id, current_user.id)

    # La extensión sale del CONTENIDO, no del nombre que mandó el cliente: un
    # `foto.jpg.svg` guardado tal cual se sirve después con JavaScript adentro.
    try:
        data, ext = await read_image_upload(file)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except UnsupportedImage as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from None

    out_dir = Path(settings.DATA_DIR) / "photos" / str(field_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    photo_id = uuid.uuid4()
    dest = out_dir / f"{photo_id}{ext}"
    dest.write_bytes(data)

    photo = FieldPhoto(
        id=photo_id,
        field_id=field_id,
        task_id=task_id,
        user_id=current_user.id,
        file_path=str(dest),
        caption=caption,
        alert_confirmed=alert_confirmed,
        lat=lat,
        lon=lon,
    )
    db.add(photo)
    await db.commit()
    await db.refresh(photo)
    out = PhotoOut.model_validate(photo)
    out.url = f"/api/v1/fields/{field_id}/photos/{photo.id}/file"
    return out


@router.get("/fields/{field_id}/photos", response_model=list[PhotoOut])
async def list_photos(field_id: uuid.UUID, current_user: CurrentUser, db: DBSession) -> list[PhotoOut]:
    await _owned_field(db, field_id, current_user.id)
    rows = (
        (
            await db.execute(
                select(FieldPhoto)
                .where(FieldPhoto.field_id == field_id)
                .order_by(FieldPhoto.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out = []
    for p in rows:
        item = PhotoOut.model_validate(p)
        item.url = f"/api/v1/fields/{field_id}/photos/{p.id}/file"
        out.append(item)
    return out


@router.get("/fields/{field_id}/photos/{photo_id}/file")
async def get_photo_file(
    field_id: uuid.UUID, photo_id: uuid.UUID, current_user: CurrentUser, db: DBSession
) -> FileResponse:
    await _owned_field(db, field_id, current_user.id)
    r = await db.execute(select(FieldPhoto).where(FieldPhoto.id == photo_id, FieldPhoto.field_id == field_id))
    photo = r.scalar_one_or_none()
    if not photo or not Path(photo.file_path).exists():
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    return FileResponse(photo.file_path)


@router.patch("/fields/{field_id}/photos/{photo_id}/label", response_model=PhotoOut)
async def label_photo(
    field_id: uuid.UUID,
    photo_id: uuid.UUID,
    body: PhotoLabelIn,
    current_user: CurrentUser,
    db: DBSession,
) -> PhotoOut:
    """Ground-truth label for a field photo — the mobile "Nuevo reporte" flow IS the
    labeling tool (Active Learning Fase 0, see docs/ACTIVE_LEARNING.md §3). Non-farmer
    label sources are admin-only for now (no dedicated agronomist role yet). Re-labeling
    a previously-audited photo clears the stale review trail.
    """
    await _owned_field(db, field_id, current_user.id)
    r = await db.execute(select(FieldPhoto).where(FieldPhoto.id == photo_id, FieldPhoto.field_id == field_id))
    photo = r.scalar_one_or_none()
    if not photo:
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    if body.label_source != "farmer" and current_user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Solo un administrador puede etiquetar como agrónomo/modelo."
        )
    if photo.reviewed_at is not None:
        photo.reviewed_by = None
        photo.reviewed_at = None
    photo.pest_key = body.pest_key
    photo.severity = body.severity
    photo.label_source = body.label_source
    await db.commit()
    await db.refresh(photo)
    out = PhotoOut.model_validate(photo)
    out.url = f"/api/v1/fields/{field_id}/photos/{photo.id}/file"
    return out


@router.post("/photos/review-queue/{photo_id}", response_model=PhotoOut)
async def review_photo(
    photo_id: uuid.UUID,
    body: PhotoReviewIn,
    admin: AdminUser,
    db: DBSession,
) -> PhotoOut:
    """Agronomist/staff audit of a farmer-labelled photo — feeds the farmer-vs-agrónomo
    agreement rate (docs/ACTIVE_LEARNING.md §3/§8). Deliberately NOT owner-scoped: this
    crosses users on purpose (a cross-ranch quality audit, not a farmer action).
    """
    r = await db.execute(select(FieldPhoto).where(FieldPhoto.id == photo_id))
    photo = r.scalar_one_or_none()
    if not photo:
        raise HTTPException(status_code=404, detail="Foto no encontrada.")
    logger.info(f"Photo {photo_id} reviewed by admin {admin.id}: agree={body.agree}")
    # reviewed_pest_key/reviewed_severity are optional (a caller may send only
    # {"agree": true}) — None means "not reviewed", not "clear the farmer's label".
    if body.reviewed_pest_key is not None:
        photo.pest_key = body.reviewed_pest_key
    if body.reviewed_severity is not None:
        photo.severity = body.reviewed_severity
    photo.reviewed_by = admin.id
    photo.reviewed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(photo)
    out = PhotoOut.model_validate(photo)
    out.url = f"/api/v1/fields/{photo.field_id}/photos/{photo.id}/file"
    return out
