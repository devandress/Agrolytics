"""Celery task for scheduled database backups."""

from loguru import logger

from app.services.backup import backup_database_to_object_storage
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.backup_tasks.run_database_backup", bind=True, max_retries=2)
def run_database_backup(self) -> dict:
    """Periodic task: dump the database to object storage, prune old backups."""
    try:
        result = backup_database_to_object_storage()
        logger.info(f"Database backup task finished: {result}")
        return result
    except Exception as exc:
        logger.error(f"Database backup failed: {exc}")
        raise self.retry(exc=exc, countdown=600) from exc  # retry after 10 min
