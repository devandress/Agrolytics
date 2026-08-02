"""Las tareas generadas pasan a proponerse; el productor decide

Hasta ahora el generador creaba tareas ya activas: el sistema decidía por el
productor y él se enteraba después. Eso está bien para un aviso, mal para una
instrucción de trabajo — nadie que conozca su campo quiere que un satélite le
llene la lista de pendientes sin preguntar.

Agrega dos estados al ciclo de vida:
  propuesta  → generada, esperando que el productor la apruebe
  descartada → el productor dijo que no corresponde

Y el registro de quién decidió, cuándo, y por qué la descartó. Ese "por qué" es lo
más valioso que produce el sistema: que alguien rechace "regar" significa que el
umbral está mal para ESE lote, y sin registrarlo esa corrección se pierde.

Las tareas existentes NO se tocan: ya fueron aceptadas de hecho por el productor,
que las viene viendo en su lista. Retroactivarlas a "propuesta" le vaciaría la
pantalla de un día para el otro.

Revision ID: 010
Revises: 009
Create Date: 2026-08-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL 12+ permite ampliar un enum dentro de una transacción siempre que
    # el valor nuevo no se USE en la misma transacción. Acá sólo se agrega.
    op.execute("ALTER TYPE task_status_enum ADD VALUE IF NOT EXISTS 'propuesta'")
    op.execute("ALTER TYPE task_status_enum ADD VALUE IF NOT EXISTS 'descartada'")

    op.add_column("field_tasks", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "field_tasks",
        sa.Column(
            "decided_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("field_tasks", sa.Column("rejection_reason", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("field_tasks", "rejection_reason")
    op.drop_column("field_tasks", "decided_by")
    op.drop_column("field_tasks", "decided_at")
    # Los valores del enum no se quitan: PostgreSQL no soporta DROP VALUE, y si
    # alguna fila quedó en 'propuesta' o 'descartada', quitarlos la rompería.
