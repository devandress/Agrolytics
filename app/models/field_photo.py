"""FieldPhoto ORM model — ground-truth photos uploaded from the field.

Photos let the foreman/worker confirm or correct an alert ("la alerta es real" /
"no hay problema"), which both validates the system and — in a future phase —
feeds an IA training data flywheel (see docs/ROADMAP.md).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FieldPhoto(Base):
    """A geotagged field photo, optionally tied to a task and an alert verdict."""

    __tablename__ = "field_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("field_tasks.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    # True = confirms the alert, False = corrects/dismisses it, None = neutral.
    alert_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    # Ground-truth taxonomy (Active Learning Fase 0, see docs/ACTIVE_LEARNING.md §2):
    # key into PEST_CATALOG, plus "sano"/"otro" for explicit negatives/catch-all.
    pest_key: Mapped[str | None] = mapped_column(String(50))
    severity: Mapped[str | None] = mapped_column(String(10))
    # Who produced the label: "farmer" (default, mobile report flow) | "agronomist" | "model_assisted".
    label_source: Mapped[str] = mapped_column(String(20), nullable=False, default="farmer")
    # How confident the ANNOTATOR was — distinct from the rule model's own confidence.
    label_confidence: Mapped[str | None] = mapped_column(String(10))
    # Auditor (agrónomo/staff) who confirmed/corrected this label, if reviewed.
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    field: Mapped["Field"] = relationship("Field", back_populates="photos")  # noqa: F821
