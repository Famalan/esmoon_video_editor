from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import JobStatus, SegmentDecision, SourceType
from shared.stages import Stage


class JobCreate(BaseModel):
    source_type: SourceType
    source_url: str | None = None


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

    model_config = {"from_attributes": True}


class JobsList(BaseModel):
    items: list[JobOut]


class ThumbnailOut(BaseModel):
    asset_id: uuid.UUID
    position_idx: int
    url: str

    model_config = {"from_attributes": True}


class SegmentOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    index: int
    start_sec: float
    end_sec: float
    title: str | None
    summary: str | None
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
    status: str

    model_config = {"from_attributes": True}


class SegmentsList(BaseModel):
    items: list[SegmentOut]


class SegmentPatch(BaseModel):
    yt_title: str | None = None
    yt_description: str | None = None
    yt_tags: list[str] | None = None
    selected_thumbnail_id: uuid.UUID | None = None
