from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import JobStatus, SourceType
from shared.stages import Stage


class JobCreate(BaseModel):
    source_type: SourceType
    source_url: str | None = None


class JobOut(BaseModel):
    id: uuid.UUID
    source_type: SourceType
    source_url: str | None
    status: JobStatus
    current_stage: Stage
    created_by: str
    created_at: datetime
    error: str | None

    model_config = {"from_attributes": True}


class JobsList(BaseModel):
    items: list[JobOut]
