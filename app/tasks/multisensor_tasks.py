"""Celery tasks for multi-sensor optical ingestion (Sentinel-2, Landsat, MODIS)."""

from loguru import logger

import app.models.field  # noqa: F401
import app.models.index  # noqa: F401
import app.models.insight  # noqa: F401
import app.models.satellite_scene  # noqa: F401
import app.models.user  # noqa: F401
from app.db.session import SyncSessionLocal
from app.services.multisensor_ingestion import ingest_sensor_for_all_fields
from app.services.sensors import AUTO_OPTICAL
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.multisensor_tasks.run_multisensor_ingestion", bind=True, max_retries=2)
def run_multisensor_ingestion(self) -> dict:
    """Pull every auto-enabled optical sensor for all fields (temporal densification)."""
    logger.info("Starting multi-sensor ingestion task.")
    results: dict[str, int] = {}
    try:
        with SyncSessionLocal() as db:
            for sensor in AUTO_OPTICAL:
                try:
                    results[sensor.key] = ingest_sensor_for_all_fields(sensor, db)
                except Exception as exc:
                    logger.error(f"Sensor {sensor.key} failed: {exc}")
                    results[sensor.key] = 0
        logger.info(f"Multi-sensor ingestion complete: {results}")
        return {"status": "ok", "records": results}
    except Exception as exc:
        logger.error(f"Multi-sensor ingestion failed: {exc}")
        raise self.retry(exc=exc, countdown=300) from exc
