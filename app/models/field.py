"""Field (parcel / lote) ORM model."""

import uuid
from datetime import UTC, date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import Date, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Field(Base):
    """Agricultural field described by a PostGIS polygon."""

    __tablename__ = "fields"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # POLYGON in WGS-84 (EPSG:4326)
    geometry: Mapped[object] = mapped_column(
        Geometry("POLYGON", srid=4326, spatial_index=True), nullable=False
    )
    area_ha: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    crop_type: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    # Planting date — blank allowed (don't invent data if the crop started unrecorded).
    planting_date: Mapped[date | None] = mapped_column(Date)
    # Expected harvest — an approximation for retrospective feedback, NOT a due date.
    expected_harvest: Mapped[date | None] = mapped_column(Date)
    # Free-text farmer context per field, fed to the AI (riego, agua, fertilizacion, riesgos, notas).
    context: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped["User"] = relationship("User", back_populates="fields")  # noqa: F821
    indices: Mapped[list["Index"]] = relationship(  # noqa: F821
        "Index", back_populates="field", cascade="all, delete-orphan"
    )
    insights: Mapped[list["Insight"]] = relationship(  # noqa: F821
        "Insight", back_populates="field", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["FieldTask"]] = relationship(  # noqa: F821
        "FieldTask", back_populates="field", cascade="all, delete-orphan"
    )
    photos: Mapped[list["FieldPhoto"]] = relationship(  # noqa: F821
        "FieldPhoto", back_populates="field", cascade="all, delete-orphan"
    )
