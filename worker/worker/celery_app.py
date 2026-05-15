from celery import Celery

from worker.config import settings

app = Celery(
    "video_slicer",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "worker.tasks.chain",
        "worker.tasks.fetch",
        "worker.tasks.transcribe",
        "worker.tasks.segment",
        "worker.tasks.cut",
        "worker.tasks.thumbnail",
        "worker.tasks.metadata",
        "worker.tasks.upload",
    ],
)

app.conf.task_default_queue = "default"
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
