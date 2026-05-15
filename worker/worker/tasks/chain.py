from celery import chain

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus
from worker.progress import publish_progress
from worker.tasks import fetch, transcribe, segment, cut, thumbnail, metadata, upload
from shared.stages import Stage


@app.task(name="worker.tasks.chain.build_pipeline")
def build_pipeline(job_id: str) -> str:
    pipeline = chain(
        fetch.run.si(job_id),
        transcribe.run.si(job_id),
        segment.run.si(job_id),
        cut.run.si(job_id),
        thumbnail.run.si(job_id),
        metadata.run.si(job_id),
        upload.run.si(job_id),
        finalize.si(job_id),
    )
    pipeline.apply_async()
    return job_id


@app.task(name="worker.tasks.chain.finalize")
def finalize(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.current_stage = Stage.DONE
        job.status = JobStatus.SUCCEEDED
        db.commit()
    publish_progress(job_id, Stage.DONE, status="succeeded")
    return job_id
