"""Tarea periódica del resumen diario."""

import asyncio

from loguru import logger

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.digest_tasks.run_daily_digest", bind=True, max_retries=1)
def run_daily_digest(self) -> dict:
    """Mandar a cada productor lo que tiene para decidir hoy.

    Un solo reintento, y a los 30 minutos: pasada esa ventana el resumen deja de
    ser "de hoy" y llega a destiempo. Es preferible saltear un día que mandar el
    resumen de la mañana a la noche, cuando la jornada ya se decidió sin él.
    """
    from app.db.session import AsyncSessionLocal
    from app.services.daily_digest import send_daily_digests

    async def _run() -> dict:
        async with AsyncSessionLocal() as db:
            return await send_daily_digests(db)

    try:
        result = asyncio.run(_run())
        logger.info(f"Resumen diario: {result}")
        return result
    except Exception as exc:
        logger.error(f"Resumen diario falló: {exc}")
        raise self.retry(exc=exc, countdown=1800) from exc
