"""Give every field task a pin, and record what that pin means

A task without coordinates is not actionable: the worker opens it and has nowhere
to go. Whole-field tasks now pin at the field centroid, located ones keep their
exact spot, and ``pin_scope`` says which is which — so the map can draw them
differently and the generator can still tell a whole-field task apart from a
located one (that job used to be done by ``lat IS NULL``).

Backfills existing rows: anything already carrying coordinates is a real point;
everything else inherits its field's centroid.

Revision ID: 009
Revises: 008
Create Date: 2026-08-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    scope = sa.Enum("campo", "punto", name="task_pin_scope_enum")
    scope.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "field_tasks",
        sa.Column("pin_scope", scope, nullable=False, server_default="campo"),
    )
    # Rows that already had coordinates were placed deliberately (farmer click or a
    # pest hotspot) — those are real points.
    op.execute("UPDATE field_tasks SET pin_scope = 'punto' WHERE lat IS NOT NULL AND lon IS NOT NULL")
    # Everything else gets its field's centroid so no task is left without a destination.
    op.execute(
        """
        UPDATE field_tasks t
           SET lat = ROUND(ST_Y(ST_Centroid(f.geometry))::numeric, 5),
               lon = ROUND(ST_X(ST_Centroid(f.geometry))::numeric, 5)
          FROM fields f
         WHERE f.id = t.field_id
           AND t.lat IS NULL
           AND f.geometry IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("field_tasks", "pin_scope")
    sa.Enum(name="task_pin_scope_enum").drop(op.get_bind(), checkfirst=True)
