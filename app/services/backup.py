"""Postgres backups to S3-compatible object storage (Cloudflare R2, etc.).

Supabase's free tier has no point-in-time recovery and no automated backups —
this is the substitute: a daily ``pg_dump -Fc`` (custom format, restorable with
``pg_restore``) streamed to object storage, with old backups pruned past
``BACKUP_RETENTION_DAYS``.

Runnable three ways:
  - As a Celery beat task (see app/tasks/backup_tasks.py) — needs a worker.
  - As a standalone script: ``python -m app.services.backup`` — for a Render
    Cron Job or any external scheduler, no worker required.
  - Called directly for a one-off manual backup before a risky migration.

No-ops (loudly, not silently) when BACKUP_S3_ENDPOINT_URL is unset — same
optional-integration pattern as DEEPSEEK_API_KEY / MERCADOPAGO_ACCESS_TOKEN.
"""

from __future__ import annotations

import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from app.core.config import settings

_PREFIX = "backups/agrolytics-"


def _configured() -> bool:
    return bool(settings.BACKUP_S3_ENDPOINT_URL and settings.BACKUP_S3_BUCKET
                and settings.BACKUP_S3_ACCESS_KEY and settings.BACKUP_S3_SECRET_KEY)


def _s3_client():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=settings.BACKUP_S3_ENDPOINT_URL,
        aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
    )


def _dump_database(dest: Path) -> None:
    """Run pg_dump in custom (compressed, restorable) format against DATABASE_URL_SYNC."""
    result = subprocess.run(
        ["pg_dump", f"--dbname={settings.DATABASE_URL_SYNC}", "-Fc", "-f", str(dest)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {result.stderr[-2000:]}")


def _prune_old_backups(client, key_now: str) -> int:
    """Delete backups older than BACKUP_RETENTION_DAYS. Returns count deleted."""
    resp = client.list_objects_v2(Bucket=settings.BACKUP_S3_BUCKET, Prefix=_PREFIX)
    objects = resp.get("Contents", [])
    cutoff = datetime.now(UTC).timestamp() - settings.BACKUP_RETENTION_DAYS * 86400
    to_delete = [o["Key"] for o in objects
                 if o["Key"] != key_now and o["LastModified"].timestamp() < cutoff]
    for key in to_delete:
        client.delete_object(Bucket=settings.BACKUP_S3_BUCKET, Key=key)
    return len(to_delete)


def backup_database_to_object_storage() -> dict:
    """Dump the database and upload it. Returns a summary dict — never raises
    on missing configuration (that's a deliberate no-op), but DOES raise if
    configured and the dump/upload itself fails, so a scheduler sees the failure.
    """
    if not _configured():
        logger.warning(
            "Backup skipped: BACKUP_S3_* not configured. Supabase free has no "
            "automatic backups — set BACKUP_S3_ENDPOINT_URL/BUCKET/ACCESS_KEY/"
            "SECRET_KEY (e.g. Cloudflare R2) before relying on this in production."
        )
        return {"status": "skipped", "reason": "not configured"}

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    key = f"{_PREFIX}{stamp}.dump"

    with tempfile.TemporaryDirectory() as tmp:
        dump_path = Path(tmp) / "backup.dump"
        logger.info("Starting database backup…")
        _dump_database(dump_path)
        size_mb = dump_path.stat().st_size / 1_048_576

        client = _s3_client()
        client.upload_file(str(dump_path), settings.BACKUP_S3_BUCKET, key)
        logger.info(f"Backup uploaded: {key} ({size_mb:.1f} MB)")

        deleted = _prune_old_backups(client, key)
        if deleted:
            logger.info(f"Pruned {deleted} backup(s) older than {settings.BACKUP_RETENTION_DAYS} days.")

    return {"status": "ok", "key": key, "size_mb": round(size_mb, 1), "pruned": deleted}


if __name__ == "__main__":
    result = backup_database_to_object_storage()
    logger.info(f"Backup result: {result}")
