import json

import redis

from worker.config import settings
from shared.stages import Stage

_redis = redis.from_url(settings.redis_url)

CHANNEL = "job-progress"


def publish_progress(job_id: str, stage: Stage, status: str = "running") -> None:
    payload = json.dumps({"job_id": job_id, "stage": stage.value, "status": status})
    _redis.publish(CHANNEL, payload)
