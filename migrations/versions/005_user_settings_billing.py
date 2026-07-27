"""Add user settings + billing fields: full_name, plan, preferences, notifications

Backs account settings (profile/preferences/notifications) and the subscription
plan used for billing limits.

Revision ID: 005
Revises: 004
Create Date: 2026-06-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("plan", sa.String(50), nullable=False, server_default="free"))
    op.add_column("users", sa.Column("preferences", postgresql.JSON(), nullable=True))
    op.add_column("users", sa.Column("notifications", postgresql.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "notifications")
    op.drop_column("users", "preferences")
    op.drop_column("users", "plan")
    op.drop_column("users", "full_name")
