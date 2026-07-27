"""Add field_tasks and field_photos tables (role-based views)

Backs the Mayordomo/Trabajador task lists and the field-photo validation flow.

Revision ID: 003
Revises: 002
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    task_type = postgresql.ENUM("riego", "fertilizacion", "inspeccion", "otro", name="task_type_enum")
    task_type.create(op.get_bind(), checkfirst=True)
    task_status = postgresql.ENUM("pendiente", "hecho", name="task_status_enum")
    task_status.create(op.get_bind(), checkfirst=True)

    # Reference the already-created types in the columns without re-emitting CREATE TYPE.
    task_type = postgresql.ENUM("riego", "fertilizacion", "inspeccion", "otro",
                                name="task_type_enum", create_type=False)
    task_status = postgresql.ENUM("pendiente", "hecho", name="task_status_enum", create_type=False)

    op.create_table(
        "field_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("task_type", task_type, nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", task_status, nullable=False, server_default="pendiente"),
        sa.Column("recommended_value", sa.String(100)),
        sa.Column("zone", sa.String(50)),
        sa.Column("due_date", sa.Date(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
    )

    op.create_table(
        "field_photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("field_tasks.id", ondelete="SET NULL")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("caption", sa.Text()),
        sa.Column("alert_confirmed", sa.Boolean()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("field_photos")
    op.drop_table("field_tasks")
    op.execute("DROP TYPE IF EXISTS task_status_enum")
    op.execute("DROP TYPE IF EXISTS task_type_enum")
