"""Celery application factory and periodic task schedule (beat)."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "agrolytics",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.satellite_tasks",
        "app.tasks.radar_tasks",
        "app.tasks.multisensor_tasks",
        "app.tasks.insight_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,
    worker_prefetch_multiplier=1,
)

# ── Periodic tasks (beat) ─────────────────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Run optical (Sentinel-2) ingestion every 6 hours.
    "ingest-satellite-scenes": {
        "task": "app.tasks.satellite_tasks.run_satellite_ingestion",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    # Run radar (Sentinel-1) ingestion every 6 hours, offset by 3 hours so the
    # two pipelines do not contend for workers/network at the same time.
    "ingest-radar-scenes": {
        "task": "app.tasks.radar_tasks.run_radar_ingestion",
        "schedule": crontab(minute=0, hour="3-23/6"),
    },
    # Multi-sensor optical (Landsat + MODIS, etc.) once a day for temporal density.
    "ingest-multisensor": {
        "task": "app.tasks.multisensor_tasks.run_multisensor_ingestion",
        "schedule": crontab(minute=30, hour=1),
    },
}
