"""Add radar (Sentinel-1) index types to index_type_enum

Adds RVI (Radar Vegetation Index) and VHVV (VH/VV cross-pol ratio) so the radar
ingestion pipeline can persist Index rows alongside the optical NDVI/NDMI/NDRE/EVI.

Revision ID: 002
Revises: 001
Create Date: 2026-06-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUES = ("RVI", "VHVV")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so escape
    # Alembic's surrounding transaction with an autocommit block. IF NOT EXISTS
    # keeps the migration idempotent across partial re-runs.
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE index_type_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum without recreating the type.
    # Rebuild the enum with only the original optical values, after detaching the
    # column default/usages. This is destructive for any rows using RVI/VHVV.
    op.execute("ALTER TYPE index_type_enum RENAME TO index_type_enum_old")
    op.execute("CREATE TYPE index_type_enum AS ENUM ('NDVI', 'NDMI', 'NDRE', 'EVI')")
    op.execute(
        "ALTER TABLE indices ALTER COLUMN index_type TYPE index_type_enum "
        "USING index_type::text::index_type_enum"
    )
    op.execute("DROP TYPE index_type_enum_old")
