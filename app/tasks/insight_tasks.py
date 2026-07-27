"""Celery tasks for on-demand insight generation."""

from loguru import logger

import app.models.field  # noqa: F401
import app.models.index  # noqa: F401
import app.models.insight  # noqa: F401
import app.models.satellite_scene  # noqa: F401

# Import all models so SQLAlchemy can resolve relationship strings
import app.models.user  # noqa: F401
from app.db.session import SyncSessionLocal
from app.services.insights_generator import generate_all_insights
from app.tasks.celery_app import celery_app


@celery_app.task(
    name="app.tasks.insight_tasks.generate_insights_task",
    bind=True,
    max_retries=2,
    soft_time_limit=300,
)
def generate_insights_task(self, field_id: str) -> dict:
    """On-demand task: compute and persist all insights for *field_id*.

    Args:
        field_id: String UUID of the target field.

    Returns:
        Dict with status and list of created insight UUIDs.
    """
    logger.info(f"Generating insights for field {field_id}")
    try:
        with SyncSessionLocal() as db:
            created_ids = generate_all_insights(field_id, db)
        logger.info(f"Insights generated for field {field_id}: {[str(i) for i in created_ids]}")
        return {"status": "ok", "field_id": field_id, "insights": [str(i) for i in created_ids]}
    except Exception as exc:
        logger.error(f"Insight generation failed for field {field_id}: {exc}")
        raise self.retry(exc=exc, countdown=60) from exc
