"""Spectral index (NDVI / NDMI / NDRE) ORM model."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Index(Base):
    """Per-field spectral index record for a given acquisition date."""

    __tablename__ = "indices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    index_type: Mapped[str] = mapped_column(
        Enum("NDVI", "NDMI", "NDRE", "EVI", "RVI", "VHVV", name="index_type_enum"),
        nullable=False,
    )
    # Path / URI to the Cloud-Optimized GeoTIFF stored on disk or S3
    raster_uri: Mapped[str | None] = mapped_column(String(1024))
    # Field-wide mean value of the index (quick query without raster)
    mean_value: Mapped[float | None] = mapped_column(Float)
    # Extra metadata: min, max, std, histogram, scene_id, etc.
    extra_meta: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    field: Mapped["Field"] = relationship("Field", back_populates="indices")  # noqa: F821
