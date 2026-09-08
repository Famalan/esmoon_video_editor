"""sources revisions and immutable runs

Revision ID: 293c87b5c74d
Revises: 0003_segment_scores
Create Date: 2026-09-06 09:24:10.936985
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '293c87b5c74d'
down_revision = '0003_segment_scores'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive migration: historical policy and validation remain unknown.
    op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'partial'")
    op.create_table('sources',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('source_key', sa.String(length=128), nullable=False),
    sa.Column('source_type', sa.String(length=16), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('filename', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('duration_sec', sa.Float(), nullable=True),
    sa.Column('original_key', sa.Text(), nullable=True),
    sa.Column('original_mime', sa.String(length=127), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('subs_key', sa.Text(), nullable=True),
    sa.Column('transcript_key', sa.Text(), nullable=True),
    sa.Column('transcript_version', sa.String(length=128), nullable=True),
    sa.Column('preview_key', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=24), server_default='pending', nullable=False),
    sa.Column('preview_status', sa.String(length=24), server_default='pending', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('ingest_attempt_id', sa.UUID(), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_key')
    )
    op.create_table('exports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='queued', nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=True),
    sa.Column('zip_key', sa.Text(), nullable=True),
    sa.Column('html_key', sa.Text(), nullable=True),
    sa.Column('pdf_key', sa.Text(), nullable=True),
    sa.Column('manifest_key', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_table('segment_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('segment_id', sa.UUID(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('start_sec', sa.Float(), nullable=False),
    sa.Column('end_sec', sa.Float(), nullable=False),
    sa.Column('attempt_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=24), server_default='queued', nullable=False),
    sa.Column('stages', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('validation', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('actual_duration_sec', sa.Float(), nullable=True),
    sa.Column('video_key', sa.Text(), nullable=True),
    sa.Column('transcript_text', sa.Text(), nullable=True),
    sa.Column('yt_title', sa.Text(), nullable=True),
    sa.Column('yt_description', sa.Text(), nullable=True),
    sa.Column('yt_tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('manual_fields', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('metadata_needs_review', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['segment_id'], ['segments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('segment_id', 'number', name='segment_revisions_number_key')
    )
    op.add_column('assets', sa.Column('revision_id', sa.UUID(), nullable=True))
    op.create_foreign_key('assets_revision_id_fkey', 'assets', 'segment_revisions', ['revision_id'], ['id'])
    op.add_column('jobs', sa.Column('source_id', sa.UUID(), nullable=True))
    op.add_column('jobs', sa.Column('analysis_version', sa.Integer(), nullable=True))
    op.add_column('jobs', sa.Column('parent_job_id', sa.UUID(), nullable=True))
    op.add_column('jobs', sa.Column('policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('jobs', sa.Column('transcript_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('jobs', sa.Column('topic', sa.Text(), nullable=True))
    op.add_column('jobs', sa.Column('audience', sa.Text(), nullable=True))
    op.add_column('jobs', sa.Column('attempt_id', sa.UUID(), nullable=True))
    op.add_column('jobs', sa.Column('attempt_no', sa.Integer(), server_default='0', nullable=False))
    op.add_column('jobs', sa.Column('idempotency_key', sa.String(length=255), nullable=True))
    op.add_column('jobs', sa.Column('request_hash', sa.String(length=64), nullable=True))
    op.add_column('jobs', sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('jobs', sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint('jobs_source_version_key', 'jobs', ['source_id', 'analysis_version'])
    op.create_unique_constraint('jobs_idempotency_key_key', 'jobs', ['idempotency_key'])
    op.create_foreign_key('jobs_parent_job_id_fkey', 'jobs', 'jobs', ['parent_job_id'], ['id'])
    op.create_foreign_key('jobs_source_id_fkey', 'jobs', 'sources', ['source_id'], ['id'])
    op.add_column('segments', sa.Column('revision', sa.Integer(), server_default='1', nullable=False))
    op.add_column('segments', sa.Column('selection', sa.String(length=16), server_default='auto', nullable=False))
    op.add_column('segments', sa.Column('review_state', sa.String(length=24), server_default='unreviewed', nullable=False))
    op.add_column('segments', sa.Column('rejection_reason', sa.Text(), nullable=True))
    op.add_column('segments', sa.Column('current_revision_id', sa.UUID(), nullable=True))
    op.create_foreign_key('segments_current_revision_fkey', 'segments', 'segment_revisions', ['current_revision_id'], ['id'], use_alter=True)
    _group_legacy_sources()



def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('segments_current_revision_fkey', 'segments', type_='foreignkey')
    op.drop_column('segments', 'current_revision_id')
    op.drop_column('segments', 'rejection_reason')
    op.drop_column('segments', 'review_state')
    op.drop_column('segments', 'selection')
    op.drop_column('segments', 'revision')
    op.drop_constraint('jobs_source_id_fkey', 'jobs', type_='foreignkey')
    op.drop_constraint('jobs_parent_job_id_fkey', 'jobs', type_='foreignkey')
    op.drop_constraint('jobs_idempotency_key_key', 'jobs', type_='unique')
    op.drop_constraint('jobs_source_version_key', 'jobs', type_='unique')
    op.drop_column('jobs', 'last_activity_at')
    op.drop_column('jobs', 'progress')
    op.drop_column('jobs', 'request_hash')
    op.drop_column('jobs', 'idempotency_key')
    op.drop_column('jobs', 'attempt_no')
    op.drop_column('jobs', 'attempt_id')
    op.drop_column('jobs', 'audience')
    op.drop_column('jobs', 'topic')
    op.drop_column('jobs', 'transcript_snapshot')
    op.drop_column('jobs', 'policy_snapshot')
    op.drop_column('jobs', 'parent_job_id')
    op.drop_column('jobs', 'analysis_version')
    op.drop_column('jobs', 'source_id')
    op.drop_constraint('assets_revision_id_fkey', 'assets', type_='foreignkey')
    op.drop_column('assets', 'revision_id')
    op.drop_table('segment_revisions')
    op.drop_table('exports')
    op.drop_table('sources')
    # ### end Alembic commands ###


def _group_legacy_sources():
    # Identity only; no new rules or transcript validation are attributed to old runs.
    import re
    import uuid
    from datetime import datetime, timezone
    from urllib.parse import urlparse, parse_qs
    connection = op.get_bind()
    sources = {}
    for row in connection.execute(sa.text("SELECT id, source_url FROM jobs WHERE source_type='url' AND source_url IS NOT NULL ORDER BY created_at")).mappings():
        parsed = urlparse(row['source_url'])
        host = (parsed.hostname or '').lower()
        parts = parsed.path.strip('/').split('/')
        video_id = ''
        if host in ('youtu.be', 'www.youtu.be'):
            video_id = parts[0]
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            video_id = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else (parts[1] if len(parts) == 2 and parts[0] in ('live', 'shorts', 'embed') else '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
            continue
        if video_id not in sources:
            source_id = uuid.uuid4()
            sources[video_id] = source_id
            connection.execute(sa.text("INSERT INTO sources(id,source_key,source_type,source_url,title,created_at,updated_at) VALUES(:id,:key,'url',:url,:title,:now,:now)"), {'id':source_id,'key':'youtube:'+video_id,'url':'https://www.youtube.com/watch?v='+video_id,'title':'YouTube · '+video_id,'now':datetime.now(timezone.utc)})
        source_id = sources[video_id]
        connection.execute(sa.text("UPDATE jobs SET source_id=:source WHERE id=:job"), {'source':source_id,'job':row['id']})
        for asset in connection.execute(sa.text("SELECT kind,s3_key,mime,size_bytes FROM assets WHERE job_id=:job AND kind IN ('source_video','source_subs')"), {'job':row['id']}).mappings():
            if asset['kind'] == 'source_video':
                connection.execute(sa.text("UPDATE sources SET original_key=COALESCE(original_key,:key),original_mime=COALESCE(original_mime,:mime),size_bytes=COALESCE(size_bytes,:size) WHERE id=:id"),{'id':source_id,'key':asset['s3_key'],'mime':asset['mime'],'size':asset['size_bytes']})
            else:
                connection.execute(sa.text("UPDATE sources SET subs_key=COALESCE(subs_key,:key) WHERE id=:id"),{'id':source_id,'key':asset['s3_key']})
