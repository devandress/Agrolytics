"""User ORM model."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    """Application user (farmer or admin)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("farmer", "admin", name="user_role"), nullable=False, default="farmer"
    )
    full_name: Mapped[str | None] = mapped_column(String(255))
    # Subscription plan key (free | pro | enterprise) — see app/services/plans.py
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default="free")
    # MercadoPago recurring subscription id (preapproval) — set once the user
    # authorizes billing, used to check status and to let them cancel it.
    mercadopago_preapproval_id: Mapped[str | None] = mapped_column(String(64))
    # UI preferences: language, units, timezone, theme
    preferences: Mapped[dict | None] = mapped_column(JSON)
    # Notification toggles: riego, plagas, email, in_app
    notifications: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    fields: Mapped[list["Field"]] = relationship(  # noqa: F821
        "Field", back_populates="user", cascade="all, delete-orphan"
    )
