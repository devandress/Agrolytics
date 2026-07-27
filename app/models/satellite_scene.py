"""Satellite scene metadata ORM model."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SatelliteScene(Base):
    """Metadata record for a single Sentinel-2 acquisition."""

    __tablename__ = "satellite_scenes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scene_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    satellite: Mapped[str] = mapped_column(String(50), nullable=False, default="sentinel-2-l2a")
    acquisition_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cloud_cover: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # URL / path to the raw scene or COG asset
    asset_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    processed_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
