from celery import Celery
from app.config import settings

celery = Celery('video_slicer', broker=settings.redis_url, backend=settings.redis_url)


def enqueue_pipeline(job_id: str, attempt_id: str) -> None:
    celery.send_task('worker.tasks.chain.build_pipeline', args=[job_id,attempt_id])


def enqueue_revision(segment_id, revision_id, attempt_id):
    celery.send_task('worker.tasks.chain.render_revision', args=[str(segment_id),str(revision_id),str(attempt_id)])


def enqueue_export(export_id, attempt_id):
    celery.send_task('worker.tasks.exports.build_export', args=[str(export_id),str(attempt_id)])
