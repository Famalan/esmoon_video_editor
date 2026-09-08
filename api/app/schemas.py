from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import JobStatus, SegmentDecision, SourceType
from shared.stages import Stage


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class JobCreate(StrictInput):
    source_type: SourceType | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    source_id: uuid.UUID | None = None
    topic: str | None = Field(default=None, max_length=4000)
    audience: str | None = Field(default=None, max_length=4000)


class RerunCreate(StrictInput):
    topic: str | None = Field(default=None, max_length=4000)
    audience: str | None = Field(default=None, max_length=4000)


class SourceOut(BaseModel):
    id: uuid.UUID
    source_type: str
    source_url: str | None
    title: str | None
    filename: str | None
    duration_sec: float | None
    status: str
    preview_status: str
    preview_url: str | None = None
    transcript_version: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ChapterOut(BaseModel):
    start_sec: float
    title: str


class JobOut(BaseModel):
    id: uuid.UUID
    source_type: SourceType
    source_url: str | None
    status: JobStatus
    current_stage: Stage
    created_by: str
    created_at: datetime
    error: str | None
    chapters: list[ChapterOut] = Field(default_factory=list)
    source_id: uuid.UUID | None = None
    source: SourceOut | None = None
    title: str | None = None
    analysis_version: int | None = None
    policy_snapshot: dict | None = None
    transcript_snapshot: dict | None = None
    topic: str | None = None
    audience: str | None = None
    attempt_no: int = 0
    progress: dict = Field(default_factory=dict)
    last_activity_at: datetime | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    model_config = ConfigDict(from_attributes=True)


class JobsList(BaseModel):
    items: list[JobOut]


class ThumbnailOut(BaseModel):
    asset_id: uuid.UUID
    position_idx: int
    url: str


class SegmentOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    index: int
    start_sec: float
    end_sec: float
    title: str | None
    summary: str | None
    transcript_excerpt: str | None = None
    relevance: int
    pain: int
    hook: int
    value: int
    decision: SegmentDecision
    yt_title: str | None
    yt_description: str | None
    yt_tags: list[str] | None
    selected_thumbnail_id: uuid.UUID | None
    thumbnails: list[ThumbnailOut]
    video_download_url: str | None
    playback_url: str | None = None
    status: str
    revision: int = 1
    current_revision_id: uuid.UUID | None = None
    media_revision: int | None = None
    selection: str = 'auto'
    selected: bool = False
    review_state: str = 'unreviewed'
    rejection_reason: str | None = None
    error: str | None = None
    stages: dict = Field(default_factory=dict)
    validation: dict = Field(default_factory=dict)
    actual_duration_sec: float | None = None
    metadata_needs_review: bool = False
    manual_fields: list[str] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class SegmentsList(BaseModel):
    items: list[SegmentOut]


class SegmentPatch(StrictInput):
    expected_revision: int = Field(ge=1)
    start_sec: float | None = Field(default=None, strict=True)
    end_sec: float | None = Field(default=None, strict=True)
    selection: Literal['auto', 'include', 'exclude'] | None = None
    review_state: Literal['unreviewed', 'accepted', 'rejected'] | None = None
    yt_title: str | None = Field(default=None, max_length=500)
    yt_description: str | None = Field(default=None, max_length=20000)
    yt_tags: list[str] | None = Field(default=None, max_length=100)
    selected_thumbnail_id: uuid.UUID | None = None
    metadata_needs_review: Literal[False] | None = None


class ExportOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    status: str
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    clip_count: int
    download_url: str | None = None
    html_url: str | None = None
    pdf_url: str | None = None
    manifest_url: str | None = None
