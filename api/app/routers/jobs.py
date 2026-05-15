from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.celery_client import enqueue_pipeline
from app.db import get_session
from app.models import Job, JobStatus, SourceType
from app.schemas import JobCreate, JobOut, JobsList
from shared.stages import Stage

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _persist_job(db: Session, source_type: SourceType, source_url: str | None, user: str) -> Job:
    job = Job(
        source_type=source_type,
        source_url=source_url,
        status=JobStatus.QUEUED,
        current_stage=Stage.FETCH,
        created_by=user,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    enqueue_pipeline(str(job.id))
    return job


@router.post("", response_model=JobOut, status_code=201)
def create_job_json(
    payload: JobCreate,
    user: Annotated[str, Header(alias="X-User")] = "anonymous",
    db: Session = Depends(get_session),
) -> Job:
    if payload.source_type == SourceType.URL and not payload.source_url:
        raise HTTPException(status_code=422, detail="source_url required for URL source")
    return _persist_job(db, payload.source_type, payload.source_url, user)


@router.post("/upload", response_model=JobOut, status_code=201)
def create_job_upload(
    file: Annotated[UploadFile, File()],
    user: Annotated[str, Header(alias="X-User")] = "anonymous",
    db: Session = Depends(get_session),
) -> Job:
    if file.content_type and not file.content_type.startswith("video/"):
        raise HTTPException(status_code=415, detail="only video/* accepted")
    return _persist_job(db, SourceType.FILE, None, user)


@router.get("", response_model=JobsList)
def list_jobs(db: Session = Depends(get_session)) -> JobsList:
    rows = db.execute(select(Job).order_by(Job.created_at.desc())).scalars().all()
    return JobsList(items=[JobOut.model_validate(j) for j in rows])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_session)) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job
