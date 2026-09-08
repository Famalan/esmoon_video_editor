from celery import Celery
from celery.signals import worker_ready, worker_shutdown
import threading

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
        "worker.tasks.exports",
    ],
)

app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1
app.conf.beat_schedule = {"worker-readiness": {"task": "worker.tasks.chain.publish_readiness", "schedule": 25.0}}

_readiness_stop = threading.Event()


@worker_ready.connect
def _start_readiness_heartbeat(**_kwargs):
    from worker.progress import publish_readiness

    _readiness_stop.clear()

    def run() -> None:
        while not _readiness_stop.is_set():
            publish_readiness()
            _readiness_stop.wait(25)

    threading.Thread(target=run, name="worker-readiness", daemon=True).start()


@worker_shutdown.connect
def _stop_readiness_heartbeat(**_kwargs):
    _readiness_stop.set()
