from celery import Celery

from app.config import settings

celery = Celery("video_slicer", broker=settings.redis_url, backend=settings.redis_url)


def enqueue_pipeline(job_id: str) -> None:
    """Кладёт цепочку пайплайна в очередь. Реализация в worker.tasks.chain."""
    celery.send_task("worker.tasks.chain.build_pipeline", args=[job_id])
