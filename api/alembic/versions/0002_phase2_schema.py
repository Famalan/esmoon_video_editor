"""phase 2 schema: expanded asset_kind, position_idx, selected_thumbnail_id

Revision ID: 0002_phase2
Revises: 405f9de6c004
Create Date: 2026-05-15 12:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_phase2"
down_revision = "405f9de6c004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) rebuild asset_kind enum: drop old, create new
    op.execute("ALTER TABLE assets DROP COLUMN kind")
    op.execute("DROP TYPE asset_kind")
    asset_kind = sa.Enum(
        "source_video", "source_subs", "source_audio",
        "transcript", "segment_video", "thumbnail",
        name="asset_kind",
    )
    asset_kind.create(op.get_bind())
    op.add_column(
        "assets",
        sa.Column("kind", asset_kind, nullable=False, server_default="source_video"),
    )
    op.alter_column("assets", "kind", server_default=None)

    # 2) add Asset.position_idx
    op.add_column(
        "assets",
        sa.Column("position_idx", sa.Integer(), nullable=True),
    )

    # 3) add Segment.selected_thumbnail_id (FK on assets.id, SET NULL)
    op.add_column(
        "segments",
        sa.Column("selected_thumbnail_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "segments_selected_thumbnail_id_fkey",
        "segments", "assets",
        ["selected_thumbnail_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("segments_selected_thumbnail_id_fkey", "segments", type_="foreignkey")
    op.drop_column("segments", "selected_thumbnail_id")
    op.drop_column("assets", "position_idx")
    op.execute("ALTER TABLE assets DROP COLUMN kind")
    op.execute("DROP TYPE asset_kind")
    asset_kind = sa.Enum("source", "transcript", "segment", "thumbnail", name="asset_kind")
    asset_kind.create(op.get_bind())
    op.add_column("assets", sa.Column("kind", asset_kind, nullable=False, server_default="source"))
    op.alter_column("assets", "kind", server_default=None)
