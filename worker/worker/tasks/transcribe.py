from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from shared.stages import Stage
from worker.celery_app import app
from worker.db import session_scope
from worker.models import JobStatus, Source
from worker.progress import publish_progress
from worker.runtime import advisory_lock, as_uuid, require_job_attempt
from worker.services import ffmpeg, storage, vtt, whisper


def _transcript_snapshot(key: str, version: str | None, body: bytes | None = None, *, duration_limit: float | None = None) -> dict:
    measured_duration = duration_limit is not None
    if duration_limit is None:
        cues = json.loads((body if body is not None else storage.download_bytes(key)).decode("utf-8"))
        if not cues:
            raise RuntimeError("Расшифровка пуста")
        duration_limit = max(float(cue["end"]) for cue in cues)
    return {
        "key": key,
        "version": version,
        "duration_limit_sec": duration_limit,
        "timing_note": ("Таймкоды ограничены фактической длительностью исходника."
                        if measured_duration else
                        "Границы основаны на таймкодах расшифровки; фактическая длительность MP4 ещё не измерена."),
    }


@app.task(name="worker.tasks.transcribe.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.TRANSCRIBE, "running", attempt_id=attempt_id, detail="Готовим расшифровку")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        attempt_id = str(job.attempt_id) if job.attempt_id else attempt_id
        source_id = str(job.source_id)
        source = db.get(Source, job.source_id)
        job_snapshot = job.transcript_snapshot
        source_snapshot = (source.transcript_key, source.transcript_version, source.duration_sec)
        needs_current_vtt = bool(source.subs_key)
    source_key, source_version, source_duration = source_snapshot
    cached = bool(job_snapshot and storage.object_exists(job_snapshot.get("key", "")))
    if cached and not job_snapshot.get("duration_limit_sec"):
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.transcript_snapshot = _transcript_snapshot(
                job_snapshot["key"], job_snapshot.get("version"), duration_limit=source_duration
            )
            db.commit()
    current_source = not needs_current_vtt or (source_version or "").startswith(vtt.TRANSCRIPT_VERSION + ":")
    if not cached and current_source and source_key and storage.object_exists(source_key):
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.transcript_snapshot = _transcript_snapshot(source_key, source_version, duration_limit=source_duration)
            db.commit()
        cached = True
    if cached:
        publish_progress(job_id, Stage.TRANSCRIBE, "done", attempt_id=attempt_id, detail="Используем сохранённую расшифровку")
        return job_id
    try:
        with advisory_lock("transcript", source_id) as db:
            require_job_attempt(db, job_id, attempt_id, lock=False)
            source = db.get(Source, as_uuid(source_id))
            current_source = not source.subs_key or (source.transcript_version or "").startswith(vtt.TRANSCRIPT_VERSION + ":")
            if current_source and source.transcript_key and storage.object_exists(source.transcript_key):
                key, version = source.transcript_key, source.transcript_version
            else:
                tmp = Path(tempfile.mkdtemp(prefix=f"transcript_{source_id}_"))
                try:
                    if source.subs_key:
                        cues = vtt.parse_vtt(storage.download_bytes(source.subs_key).decode("utf-8"))
                    elif source.original_key:
                        video, audio = tmp / "source", tmp / "audio.mp3"
                        storage.download_file(source.original_key, video)
                        ffmpeg.extract_audio(src=video, dst=audio)
                        cues = [dict(c) for c in whisper.transcribe_audio(audio, language="ru")]
                    else:
                        raise RuntimeError("YouTube не отдал расшифровку. Прикрепите расшифровку с таймкодами; видео скачиваться не будет.")
                    if not cues:
                        raise RuntimeError("Расшифровка пуста")
                    body = json.dumps(cues, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                    version = hashlib.sha256(body).hexdigest()
                    if source.subs_key:
                        version = vtt.TRANSCRIPT_VERSION + ":" + version
                    key = f"sources/{source.id}/transcripts/{version}.json"
                    if not storage.object_exists(key):
                        storage.upload_bytes(body, key, "application/json")
                    source.transcript_key, source.transcript_version = key, version
                    source.updated_at = datetime.now(UTC)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.transcript_snapshot = _transcript_snapshot(key, version, duration_limit=source_duration)
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.status, job.error = JobStatus.FAILED, f"transcribe: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.TRANSCRIBE, "failed", attempt_id=attempt_id, detail=str(exc)[:300])
        raise
    publish_progress(job_id, Stage.TRANSCRIBE, "done", attempt_id=attempt_id, detail="Расшифровка готова")
    return job_id
