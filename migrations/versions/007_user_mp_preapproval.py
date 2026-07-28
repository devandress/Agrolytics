"""Add MercadoPago preapproval (subscription) id to users

Needed to check/cancel the recurring subscription later — Checkout Pro's
one-time preference didn't need this, but preapproval (Suscripciones) does.

Revision ID: 007
Revises: 006
Create Date: 2026-07-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mercadopago_preapproval_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "mercadopago_preapproval_id")
