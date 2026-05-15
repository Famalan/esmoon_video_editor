from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job
from worker.progress import publish_progress
from shared.stages import Stage


@app.task(name="worker.tasks.metadata.run")
def run(job_id: str) -> str:
    with session_scope() as db:
        job = db.get(Job, job_id)
        job.current_stage = Stage.METADATA
        db.commit()
    publish_progress(job_id, Stage.METADATA)
    return job_id
