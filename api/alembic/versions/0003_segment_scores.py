"""add content scores and publication decision to segments

Revision ID: 0003_segment_scores
Revises: 0002_phase2
Create Date: 2026-08-30 16:10:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_segment_scores"
down_revision = "0002_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    segment_decision = sa.Enum("publish", "skip", name="segment_decision")
    segment_decision.create(op.get_bind())

    op.add_column(
        "jobs",
        sa.Column(
            "chapters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.add_column(
        "segments",
        sa.Column("relevance", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "segments",
        sa.Column("pain", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "segments",
        sa.Column("hook", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "segments",
        sa.Column("value", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "segments",
        sa.Column(
            "decision",
            segment_decision,
            nullable=False,
            server_default="publish",
        ),
    )

    op.create_check_constraint(
        "segments_relevance_range", "segments", "relevance BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "segments_pain_range", "segments", "pain BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "segments_hook_range", "segments", "hook BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "segments_value_range", "segments", "value BETWEEN 0 AND 100"
    )


def downgrade() -> None:
    op.drop_constraint("segments_value_range", "segments", type_="check")
    op.drop_constraint("segments_hook_range", "segments", type_="check")
    op.drop_constraint("segments_pain_range", "segments", type_="check")
    op.drop_constraint("segments_relevance_range", "segments", type_="check")
    op.drop_column("segments", "decision")
    op.drop_column("segments", "value")
    op.drop_column("segments", "hook")
    op.drop_column("segments", "pain")
    op.drop_column("segments", "relevance")
    op.drop_column("jobs", "chapters")
    op.execute("DROP TYPE segment_decision")
