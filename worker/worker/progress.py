from __future__ import annotations

import json
import shutil
import subprocess
import threading
from datetime import UTC, datetime

import redis
from sqlalchemy import update

from shared.stages import Stage
from shared.policy import POLICY
from worker.config import settings

_redis = redis.from_url(settings.redis_url)
CHANNEL = "job-progress"
READINESS_KEY = "video-slicer:worker-ready"
_heartbeat_lock = threading.Lock()
_heartbeats: dict[tuple[str, str | None], threading.Event] = {}


def _ensure_job_heartbeat(job_id: str, attempt_id: str | None) -> None:
    key = (job_id, attempt_id)
    with _heartbeat_lock:
        if key in _heartbeats and not _heartbeats[key].is_set():
            return
        stop = threading.Event()
        _heartbeats[key] = stop

    def beat() -> None:
        from worker.db import session_scope
        from worker.models import Job

        while not stop.wait(20):
            with session_scope() as db:
                statement = update(Job).where(Job.id == job_id)
                statement = statement.where(Job.attempt_id == attempt_id) if attempt_id else statement.where(Job.attempt_id.is_(None))
                result = db.execute(statement.values(last_activity_at=datetime.now(UTC)))
                if result.rowcount != 1:
                    break
                db.commit()
        with _heartbeat_lock:
            if _heartbeats.get(key) is stop:
                _heartbeats.pop(key, None)

    threading.Thread(target=beat, name=f"job-heartbeat-{job_id}", daemon=True).start()


def _stop_job_heartbeat(job_id: str, attempt_id: str | None) -> None:
    key = (job_id, attempt_id)
    with _heartbeat_lock:
        stop = _heartbeats.pop(key, None)
    if stop:
        stop.set()


def publish_progress(job_id: str, stage: Stage, status: str = "running", *, attempt_id: str | None = None, detail: str = "", completed: int | None = None, total: int | None = None) -> None:
    from worker.db import session_scope
    from worker.runtime import require_job_attempt

    progress = {"stage": stage.value, "status": status, "detail": detail}
    if completed is not None:
        progress["completed"] = completed
    if total is not None:
        progress["total"] = total
        progress["percent"] = round(completed * 100 / total, 1) if completed is not None and total else 0
    now = datetime.now(UTC)
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        captured_attempt = str(job.attempt_id) if job.attempt_id else None
        previous = dict(job.progress or {})
        if job.current_stage != stage:
            for field in ("completed", "total", "percent"):
                previous.pop(field, None)
        job.current_stage = stage
        job.progress = {**previous, **progress}
        job.last_activity_at = now
        db.commit()
    if status == "running":
        _ensure_job_heartbeat(job_id, captured_attempt)
    else:
        _stop_job_heartbeat(job_id, attempt_id)
    try:
        _redis.publish(CHANNEL, json.dumps({"job_id": job_id, **progress}, ensure_ascii=False))
    except redis.RedisError:
        pass


def publish_readiness() -> dict:
    ffmpeg_ok = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    codex_ok = False
    details: list[str] = []
    try:
        check = subprocess.run([settings.codex_cli_path, "login", "status"], capture_output=True, text=True, timeout=15, check=False)
        codex_ok = check.returncode == 0
        if not codex_ok:
            details.append("Codex CLI не авторизован")
    except (OSError, subprocess.TimeoutExpired):
        details.append("Codex CLI недоступен")
    if not ffmpeg_ok:
        details.append("ffmpeg/ffprobe недоступны")
    payload = {
        "ready": codex_ok and ffmpeg_ok, "codex": codex_ok, "ffmpeg": ffmpeg_ok,
        "model": POLICY["model"], "reasoning": POLICY["reasoning"],
        "detail": "; ".join(details) or "Обработчик готов", "updated_at": datetime.now(UTC).isoformat(),
    }
    try:
        _redis.setex(READINESS_KEY, 90, json.dumps(payload, ensure_ascii=False))
    except redis.RedisError:
        pass
    return payload
