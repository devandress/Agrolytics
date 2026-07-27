"""Insight ORM model (management zones, stress alerts, prescriptions, yield estimates)."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Insight(Base):
    """AI-generated insight for a field that may require purchase before full access."""

    __tablename__ = "insights"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    insight_type: Mapped[str] = mapped_column(
        Enum(
            "zone_map",
            "stress_alert",
            "prescription",
            "yield_est",
            name="insight_type_enum",
        ),
        nullable=False,
    )
    # Full JSON payload (GeoJSON features, prescription tables, etc.)
    content: Mapped[dict | None] = mapped_column(JSON)
    is_purchased: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False, default=4.99)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    field: Mapped["Field"] = relationship("Field", back_populates="insights")  # noqa: F821
