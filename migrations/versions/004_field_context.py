"""Add per-field farmer context: planting_date, expected_harvest, context JSON

Captures the grower's natural-language context per field (irrigation system,
fertilisation, risks) plus planting/harvest dates, to feed the AI toward the
goals of reducing water/fertiliser and preventing losses.

Revision ID: 004
Revises: 003
Create Date: 2026-06-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fields", sa.Column("planting_date", sa.Date(), nullable=True))
    op.add_column("fields", sa.Column("expected_harvest", sa.Date(), nullable=True))
    op.add_column("fields", sa.Column("context", postgresql.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("fields", "context")
    op.drop_column("fields", "expected_harvest")
    op.drop_column("fields", "planting_date")
