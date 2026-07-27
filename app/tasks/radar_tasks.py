"""Celery tasks for Sentinel-1 SAR (radar) ingestion."""

from loguru import logger

import app.models.field  # noqa: F401
import app.models.index  # noqa: F401
import app.models.insight  # noqa: F401
import app.models.satellite_scene  # noqa: F401

# Import all models so SQLAlchemy can resolve relationship strings
import app.models.user  # noqa: F401
from app.db.session import SyncSessionLocal
from app.services.radar_ingestion import ingest_radar_for_fields
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.radar_tasks.run_radar_ingestion", bind=True, max_retries=3)
def run_radar_ingestion(self) -> dict:
    """Periodic task: search for new Sentinel-1 RTC scenes and process them.

    Radar is cloud-independent, so this keeps the vegetation signal flowing even
    when the optical (Sentinel-2) pipeline finds no clear scenes.
    """
    logger.info("Starting radar (Sentinel-1) ingestion task.")
    try:
        with SyncSessionLocal() as db:
            new_records = ingest_radar_for_fields(db)
        logger.info(f"Radar ingestion complete: {new_records} new index records.")
        return {"status": "ok", "new_records": new_records}
    except Exception as exc:
        logger.error(f"Radar ingestion failed: {exc}")
        raise self.retry(exc=exc, countdown=300) from exc  # retry after 5 min
