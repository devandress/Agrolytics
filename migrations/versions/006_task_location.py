"""Add lat/lon to field_tasks (task map points)

Lets tasks live at a specific point on the map — created by the farmer (click) or
by the AI — so the map shows where each action must happen.

Revision ID: 006
Revises: 005
Create Date: 2026-06-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("field_tasks", sa.Column("lat", sa.Float(), nullable=True))
    op.add_column("field_tasks", sa.Column("lon", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("field_tasks", "lon")
    op.drop_column("field_tasks", "lat")
