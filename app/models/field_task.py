"""FieldTask ORM model — actionable to-do items derived from field conditions.

Tasks power the Mayordomo (foreman) and Trabajador (worker) role views: a
prioritised list of "what to do today" per field (irrigate, fertilise, scout),
generated from the latest spectral indices and crop thresholds.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FieldTask(Base):
    """A single actionable task for a field (e.g. 'Regar 25 mm')."""

    __tablename__ = "field_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(
        Enum("riego", "fertilizacion", "inspeccion", "otro", name="task_type_enum"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    # 1 = urgent … 4 = low. Used for sorting the daily list.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    status: Mapped[str] = mapped_column(
        Enum("pendiente", "hecho", name="task_status_enum"), nullable=False, default="pendiente"
    )
    # Human-readable recommended quantity, e.g. "25 mm" or "120 kg N/ha".
    recommended_value: Mapped[str | None] = mapped_column(String(100))
    zone: Mapped[str | None] = mapped_column(String(50))
    # Map point where the task must happen (created by farmer click or AI).
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    field: Mapped["Field"] = relationship("Field", back_populates="tasks")  # noqa: F821
