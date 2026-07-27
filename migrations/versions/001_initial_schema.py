"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    user_role = postgresql.ENUM("farmer", "admin", name="user_role")
    user_role.create(op.get_bind(), checkfirst=True)

    index_type_enum = postgresql.ENUM("NDVI", "NDMI", "NDRE", "EVI", name="index_type_enum")
    index_type_enum.create(op.get_bind(), checkfirst=True)

    insight_type_enum = postgresql.ENUM(
        "zone_map", "stress_alert", "prescription", "yield_est",
        name="insight_type_enum",
    )
    insight_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", postgresql.ENUM("farmer", "admin", name="user_role", create_type=False), nullable=False, server_default="farmer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("geometry", Geometry("POLYGON", srid=4326, spatial_index=True), nullable=False),
        sa.Column("area_ha", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("crop_type", sa.String(100)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_fields_user_id", "fields", ["user_id"])

    op.create_table(
        "indices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fields.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("index_type", postgresql.ENUM("NDVI", "NDMI", "NDRE", "EVI", name="index_type_enum", create_type=False), nullable=False),
        sa.Column("raster_uri", sa.String(1024)),
        sa.Column("mean_value", sa.Float),
        sa.Column("extra_meta", postgresql.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_indices_field_id", "indices", ["field_id"])
    op.create_index("ix_indices_date", "indices", ["date"])

    op.create_table(
        "insights",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fields.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column(
            "insight_type",
            postgresql.ENUM("zone_map", "stress_alert", "prescription", "yield_est", name="insight_type_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("content", postgresql.JSON),
        sa.Column("is_purchased", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("price_usd", sa.Float, nullable=False, server_default="4.99"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_insights_field_id", "insights", ["field_id"])
    op.create_index("ix_insights_date", "insights", ["date"])

    op.create_table(
        "satellite_scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scene_id", sa.String(255), nullable=False, unique=True),
        sa.Column("satellite", sa.String(50), nullable=False, server_default="sentinel-2-l2a"),
        sa.Column("acquisition_date", sa.Date, nullable=False),
        sa.Column("cloud_cover", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("asset_url", sa.String(1024), nullable=False),
        sa.Column("processed_flag", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_satellite_scenes_scene_id", "satellite_scenes", ["scene_id"], unique=True)
    op.create_index("ix_satellite_scenes_acquisition_date", "satellite_scenes", ["acquisition_date"])


def downgrade() -> None:
    op.drop_table("satellite_scenes")
    op.drop_table("insights")
    op.drop_table("indices")
    op.drop_table("fields")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS insight_type_enum")
    op.execute("DROP TYPE IF EXISTS index_type_enum")
    op.execute("DROP TYPE IF EXISTS user_role")
