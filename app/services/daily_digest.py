"""El resumen diario: lo único que hace proactivo al sistema.

Todo lo demás ya funcionaba solo — la ingesta corre cada 6 horas, el generador
propone tareas, el modelo de plagas marca focos, el muestreo elige de qué zonas
pedir foto. Y después el sistema **se quedaba esperando a que el productor abriera
la aplicación.** Un agricultor no abre un tablero todos los días: está en el campo.
Si el aviso espera a que él entre, llega tarde o no llega.

Este módulo cierra ese último metro.

Tres decisiones de diseño que valen más que el código:

1. **Si no hay nada que decir, no se manda nada.** Un correo diario que la mitad de
   los días dice "todo bien" enseña a ignorarlo, y el día que importa ya está
   filtrado mentalmente. El silencio también es información.

2. **El correo es una decisión, no un informe.** Arranca con lo que hay que hacer
   hoy. Los números están para respaldar, nunca al revés.

3. **El texto se arma con reglas, no con un modelo de lenguaje.** Este correo puede
   terminar en una decisión de aplicar un producto sobre un cultivo. Que la
   redacción sea determinista y testeable importa más que sonar natural. El modelo
   ya escribe el reporte largo, que se pide a propósito.
"""

from __future__ import annotations

import uuid
from datetime import date

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.field import Field
from app.models.field_task import FieldTask
from app.models.user import User
from app.services.email import send_email

_CAT = {
    "riego": "Riego",
    "fertilizacion": "Fertilización",
    "inspeccion": "Revisar",
    "otro": "Tarea",
}
_MAX_LINEAS = 12


def _linea(task: FieldTask, field_name: str) -> str:
    etiqueta = _CAT.get(task.task_type, "Tarea")
    detalle = (task.detail or task.title or "").strip()
    extra = f" ({task.recommended_value})" if task.recommended_value else ""
    donde = "" if task.pin_scope == "punto" else " — toda la parcela"
    return f"  - [{etiqueta}] {field_name}: {detalle}{extra}{donde}"


def compose_digest(
    propuestas: list[tuple[FieldTask, str]],
    pendientes: list[tuple[FieldTask, str]],
    base_url: str,
    hoy: date | None = None,
) -> tuple[str, str] | None:
    """Asunto y cuerpo del resumen, o ``None`` si no hay nada que contar.

    Devolver ``None`` es una función del módulo, no un caso borde: es lo que evita
    que el correo se vuelva ruido.
    """
    if not propuestas and not pendientes:
        return None

    hoy = hoy or date.today()
    urgentes = [p for p in propuestas if (p[0].priority or 3) <= 1]

    # El asunto tiene que decidirse desde la bandeja de entrada, sin abrir nada.
    if urgentes:
        asunto = f"Agrolytics: {len(urgentes)} urgente(s) para revisar hoy"
    elif propuestas:
        asunto = f"Agrolytics: {len(propuestas)} tarea(s) para aprobar"
    else:
        asunto = f"Agrolytics: {len(pendientes)} tarea(s) pendientes"

    partes = [f"Resumen del {hoy.strftime('%d/%m/%Y')}", ""]

    if propuestas:
        partes.append(f"EL SISTEMA PROPONE {len(propuestas)} TAREA(S)")
        partes.append("Salen de las imágenes satelitales. Vos decidís si corresponden.")
        partes.append("")
        for task, field_name in propuestas[:_MAX_LINEAS]:
            partes.append(_linea(task, field_name))
        if len(propuestas) > _MAX_LINEAS:
            partes.append(f"  … y {len(propuestas) - _MAX_LINEAS} más.")
        partes.append("")
        partes.append(f"Aprobar o descartar: {base_url}")
        partes.append("")

    if pendientes:
        partes.append(f"YA APROBADAS, SIN HACER: {len(pendientes)}")
        for task, field_name in pendientes[:_MAX_LINEAS]:
            partes.append(_linea(task, field_name))
        if len(pendientes) > _MAX_LINEAS:
            partes.append(f"  … y {len(pendientes) - _MAX_LINEAS} más.")
        partes.append("")

    partes.append("—")
    partes.append("Sólo te escribimos cuando hay algo que decidir o hacer.")
    return asunto, "\n".join(partes)


async def _open_tasks(db: AsyncSession, user_id: uuid.UUID, status: str):
    rows = (
        await db.execute(
            select(FieldTask, Field.name)
            .join(Field, FieldTask.field_id == Field.id)
            .where(Field.user_id == user_id, FieldTask.status == status)
            .order_by(FieldTask.priority.asc(), FieldTask.due_date.asc())
        )
    ).all()
    return list(rows)


async def send_digest_for_user(db: AsyncSession, user: User) -> bool:
    """Armar y mandar el resumen de un usuario. Devuelve si se envió."""
    propuestas = await _open_tasks(db, user.id, "propuesta")
    pendientes = await _open_tasks(db, user.id, "pendiente")
    armado = compose_digest(propuestas, pendientes, settings.PUBLIC_BASE_URL)
    if armado is None:
        return False
    asunto, cuerpo = armado
    return send_email(user.email, asunto, cuerpo)


async def send_daily_digests(db: AsyncSession) -> dict[str, int]:
    """Recorrer los usuarios y mandar el resumen a los que tengan algo que decidir."""
    users = (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    enviados = 0
    for user in users:
        try:
            if await send_digest_for_user(db, user):
                enviados += 1
        except Exception as exc:  # un usuario roto no puede frenar al resto
            logger.error(f"Resumen diario falló para {user.email}: {exc}")
    logger.info(f"Resumen diario: {enviados} enviado(s) de {len(users)} usuario(s)")
    return {"users": len(users), "sent": enviados}
