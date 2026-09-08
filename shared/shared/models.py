from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, BigInteger, Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from shared.stages import Stage


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceType(StrEnum):
    FILE = "file"
    URL = "url"


class AssetKind(StrEnum):
    SOURCE_VIDEO = "source_video"
    SOURCE_SUBS = "source_subs"
    SOURCE_AUDIO = "source_audio"
    TRANSCRIPT = "transcript"
    SEGMENT_VIDEO = "segment_video"
    THUMBNAIL = "thumbnail"


class SegmentStatus(StrEnum):
    PENDING = "pending"
    CUT = "cut"
    THUMBNAIL_READY = "thumbnail_ready"
    METADATA_READY = "metadata_ready"
    UPLOADED = "uploaded"
    FAILED = "failed"


class SegmentDecision(StrEnum):
    PUBLISH = "publish"
    SKIP = "skip"


class UploadStatus(StrEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source_id", "analysis_version", name="jobs_source_version_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type", values_callable=lambda obj: [e.value for e in obj]))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status", values_callable=lambda obj: [e.value for e in obj]), default=JobStatus.QUEUED)
    current_stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage", values_callable=lambda obj: [e.value for e in obj]), default=Stage.FETCH)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)

    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    analysis_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    policy_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    transcript_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    segments: Mapped[list[Segment]] = relationship(back_populates="job", cascade="all, delete-orphan")
    assets: Mapped[list[Asset]] = relationship(back_populates="job", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    kind: Mapped[AssetKind] = mapped_column(Enum(AssetKind, name="asset_kind", values_callable=lambda obj: [e.value for e in obj]))
    s3_key: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segments.id", ondelete="CASCADE"), nullable=True
    )
    position_idx: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segment_revisions.id"), nullable=True)

    job: Mapped[Job] = relationship(back_populates="assets")
    __table_args__ = (Index("assets_revision_slot_key", revision_id, kind, func.coalesce(position_idx, -1), unique=True, postgresql_where=revision_id.is_not(None)),)


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("job_id", "index", name="segments_job_index_key"),
        CheckConstraint("relevance BETWEEN 0 AND 100", name="segments_relevance_range"),
        CheckConstraint("pain BETWEEN 0 AND 100", name="segments_pain_range"),
        CheckConstraint("hook BETWEEN 0 AND 100", name="segments_hook_range"),
        CheckConstraint("value BETWEEN 0 AND 100", name="segments_value_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance: Mapped[int] = mapped_column(Integer, default=100)
    pain: Mapped[int] = mapped_column(Integer, default=100)
    hook: Mapped[int] = mapped_column(Integer, default=100)
    value: Mapped[int] = mapped_column(Integer, default=100)
    decision: Mapped[SegmentDecision] = mapped_column(
        Enum(
            SegmentDecision,
            name="segment_decision",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=SegmentDecision.PUBLISH,
    )
    status: Mapped[SegmentStatus] = mapped_column(Enum(SegmentStatus, name="segment_status", values_callable=lambda obj: [e.value for e in obj]), default=SegmentStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_thumbnail_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "assets.id",
            ondelete="SET NULL",
            use_alter=True,
            name="segments_selected_thumbnail_id_fkey",
        ),
        nullable=True,
    )

    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    selection: Mapped[str] = mapped_column(String(16), default="auto", server_default="auto")
    review_state: Mapped[str] = mapped_column(String(24), default="unreviewed", server_default="unreviewed")
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segment_revisions.id", use_alter=True, name="segments_current_revision_fkey"), nullable=True)

    job: Mapped[Job] = relationship(back_populates="segments")
    upload: Mapped[Upload | None] = relationship(back_populates="segment", uselist=False, cascade="all, delete-orphan")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), unique=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    youtube_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus, name="upload_status", values_callable=lambda obj: [e.value for e in obj]), default=UploadStatus.PENDING)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    segment: Mapped[Segment] = relationship(back_populates="upload")


class YouTubeAccount(Base):
    __tablename__ = "youtube_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    channel_id: Mapped[str] = mapped_column(String(255), unique=True)
    refresh_token: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted at rest
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_key: Mapped[str] = mapped_column(String(128), unique=True)
    source_type: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_mime: Mapped[str | None] = mapped_column(String(127), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subs_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    preview_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    preview_status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class SegmentRevision(Base):
    __tablename__ = "segment_revisions"
    __table_args__ = (UniqueConstraint("segment_id", "number", name="segment_revisions_number_key"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    start_sec: Mapped[float] = mapped_column(Float)
    end_sec: Mapped[float] = mapped_column(Float)
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_uuid)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    stages: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    validation: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    actual_duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    yt_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    yt_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    yt_tags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    manual_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), default=_uuid)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    zip_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
