"""Celery tasks for satellite data ingestion."""

from loguru import logger

import app.models.field  # noqa: F401
import app.models.index  # noqa: F401
import app.models.insight  # noqa: F401
import app.models.satellite_scene  # noqa: F401

# Import all models so SQLAlchemy can resolve relationship strings
import app.models.user  # noqa: F401
from app.db.session import SyncSessionLocal
from app.services.satellite_ingestion import ingest_scenes_for_fields
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.satellite_tasks.run_satellite_ingestion", bind=True, max_retries=3)
def run_satellite_ingestion(self) -> dict:
    """Periodic task: search for new Sentinel-2 scenes and process them.

    Returns a summary dict with the number of new index records created.
    """
    logger.info("Starting satellite ingestion task.")
    try:
        with SyncSessionLocal() as db:
            new_records = ingest_scenes_for_fields(db)
        logger.info(f"Satellite ingestion complete: {new_records} new index records.")
        return {"status": "ok", "new_records": new_records}
    except Exception as exc:
        logger.error(f"Satellite ingestion failed: {exc}")
        raise self.retry(exc=exc, countdown=300) from exc  # retry after 5 min
