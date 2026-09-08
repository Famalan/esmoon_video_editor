from __future__ import annotations

import uuid
from contextlib import contextmanager
from functools import wraps
from datetime import UTC, datetime

from sqlalchemy import select, text

from shared.policy import analysis_duration, is_selected, validate_selected
from worker.db import session_scope
from worker.db import engine
from worker.models import Job, JobStatus, Segment, SegmentRevision, Source


class StaleAttempt(RuntimeError):
    pass


def as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def require_job_attempt(db, job_id: str, attempt_id: str | None, *, lock: bool = True) -> Job:
    statement = select(Job).where(Job.id == as_uuid(job_id)).execution_options(populate_existing=True)
    job = db.scalar(statement.with_for_update() if lock else statement)
    if job is None:
        raise StaleAttempt("job no longer exists")
    expected = as_uuid(attempt_id)
    if (expected is None and job.attempt_id is not None) or (expected is not None and job.attempt_id != expected):
        raise StaleAttempt("job attempt was superseded")
    return job


def require_revision_attempt(db, segment_id: str, revision_id: str, attempt_id: str) -> tuple[Segment, SegmentRevision]:
    segment_id = as_uuid(segment_id)
    revision_id = as_uuid(revision_id)
    probe = db.get(Segment, segment_id)
    if probe is None:
        raise StaleAttempt("segment revision no longer exists")
    db.scalar(select(Job).where(Job.id == probe.job_id).with_for_update())
    segment = db.scalar(select(Segment).where(Segment.id == segment_id).with_for_update().execution_options(populate_existing=True))
    revision = db.scalar(select(SegmentRevision).where(SegmentRevision.id == revision_id).with_for_update().execution_options(populate_existing=True))
    if segment is None or revision is None or revision.segment_id != segment.id:
        raise StaleAttempt("segment revision no longer exists")
    if segment.current_revision_id != revision.id or revision.attempt_id != as_uuid(attempt_id):
        raise StaleAttempt("segment revision was superseded")
    return segment, revision


@contextmanager
def advisory_lock(namespace: str, identifier: str):
    with session_scope() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"{namespace}:{identifier}"})
        yield db
        db.commit()


@contextmanager
def session_advisory_lock(namespace: str, identifier: str):
    key = f"{namespace}:{identifier}"
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": key})
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
            connection.commit()


def serialized_stage(stage):
    """Deduplicate long stage work without holding row locks during subprocesses."""
    def decorate(function):
        @wraps(function)
        def run(segment_id, revision_id, attempt_id):
            with session_advisory_lock(stage, revision_id):
                return function(segment_id, revision_id, attempt_id)
        return run
    return decorate


def selected_revisions(db, job: Job) -> list[tuple[Segment, SegmentRevision]]:
    rows = db.execute(select(Segment, SegmentRevision).join(SegmentRevision, Segment.current_revision_id == SegmentRevision.id).where(Segment.job_id == job.id).order_by(Segment.index)).all()
    selected = [(segment, revision) for segment, revision in rows if is_selected(segment)]
    if job.source_id:
        source = db.get(Source, job.source_id)
        if source:
            validate_selected(
                [{"start_sec": revision.start_sec, "end_sec": revision.end_sec} for _, revision in selected],
                analysis_duration(job, source),
            )
    return selected


def aggregate_job(job_id: str, attempt_id: str | None) -> JobStatus:
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        rows = selected_revisions(db, job)
        source = db.get(Source, job.source_id) if job.source_id else None
        media_deferred = not bool(source and source.original_key)
        for segment, revision in rows:
            # Recovery may finish an earlier stage after metadata already succeeded.
            # Reconcile from independent stage outcomes, not the last task to run.
            if (all(revision.stages.get(name) == "succeeded" for name in ("render", "verify", "thumbnail", "metadata"))
                    and revision.validation.get("technical", {}).get("ok") is True
                    and revision.validation.get("narrative", {}).get("ok") is True):
                revision.status, revision.error, segment.error = "ready", None, None
            elif (media_deferred and revision.validation.get("narrative", {}).get("ok") is True
                  and not revision.video_key):
                revision.status, revision.error, segment.error = "analyzed", None, None
        states = [revision.status for _, revision in rows]
        has_technical_output = any(revision.validation.get("technical", {}).get("ok") is True for _, revision in rows)
        if not states or all(state in ("ready", "analyzed") for state in states):
            status = JobStatus.SUCCEEDED
        elif any(state in ("failed", "rejected") for state in states) and (any(state in ("ready", "analyzed") for state in states) or has_technical_output):
            status = JobStatus.PARTIAL
        elif states and all(state in ("failed", "rejected") for state in states):
            status = JobStatus.FAILED
        else:
            status = JobStatus.RUNNING
        job.status = status
        job.progress = {**(job.progress or {}), "media_deferred": media_deferred}
        job.last_activity_at = datetime.now(UTC)
        db.commit()
        return status
