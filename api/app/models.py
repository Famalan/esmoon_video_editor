from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ARRAY, BigInteger, CheckConstraint, DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from shared.stages import Stage


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType, name="source_type", values_callable=lambda obj: [e.value for e in obj]))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status", values_callable=lambda obj: [e.value for e in obj]), default=JobStatus.QUEUED)
    current_stage: Mapped[Stage] = mapped_column(Enum(Stage, name="stage", values_callable=lambda obj: [e.value for e in obj]), default=Stage.FETCH)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chapters: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)

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

    job: Mapped[Job] = relationship(back_populates="assets")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (
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
