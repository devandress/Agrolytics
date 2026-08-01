"""Add pest/severity ground-truth labels + audit trail to field_photos

Active Learning Fase 0 (see docs/ACTIVE_LEARNING.md §2): FieldPhoto only had
alert_confirmed (bool|None) so far — not enough to train a species classifier.
Adds pest_key/severity/label_source/label_confidence plus a lightweight
agronomist-review trail (reviewed_by/reviewed_at).

Revision ID: 008
Revises: 007
Create Date: 2026-07-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("field_photos", sa.Column("pest_key", sa.String(length=50), nullable=True))
    op.add_column("field_photos", sa.Column("severity", sa.String(length=10), nullable=True))
    op.add_column(
        "field_photos",
        sa.Column("label_source", sa.String(length=20), nullable=False, server_default="farmer"),
    )
    op.add_column("field_photos", sa.Column("label_confidence", sa.String(length=10), nullable=True))
    op.add_column(
        "field_photos",
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("field_photos", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("field_photos", "reviewed_at")
    op.drop_column("field_photos", "reviewed_by")
    op.drop_column("field_photos", "label_confidence")
    op.drop_column("field_photos", "label_source")
    op.drop_column("field_photos", "severity")
    op.drop_column("field_photos", "pest_key")
