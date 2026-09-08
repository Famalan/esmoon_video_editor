"""Prevent duplicate candidates and duplicate output slots on task redelivery."""
from alembic import op
import sqlalchemy as sa

revision = '0005_output_uniqueness'
down_revision = '293c87b5c74d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint('segments_job_index_key','segments',['job_id','index'])
    op.create_index('assets_revision_slot_key','assets',['revision_id','kind',sa.text('COALESCE(position_idx, -1)')],unique=True,postgresql_where=sa.text('revision_id IS NOT NULL'))


def downgrade():
    op.drop_index('assets_revision_slot_key','assets')
    op.drop_constraint('segments_job_index_key','segments',type_='unique')
