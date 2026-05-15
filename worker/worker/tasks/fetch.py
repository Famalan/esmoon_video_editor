from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus
from worker.progress import publish_progress
from shared.stages import Stage


@app.task(name="worker.tasks.fetch.run")
def run(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            raise RuntimeError(f"job {job_id} not found")
        job.status = JobStatus.RUNNING
        job.current_stage = Stage.FETCH
        db.commit()
    publish_progress(job_id, Stage.FETCH)
    return job_id
