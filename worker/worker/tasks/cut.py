from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from shared.policy import analysis_duration, validate_actual_duration, validate_interval
from shared.stages import Stage
from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, SegmentStatus, Source
from worker.progress import publish_progress
from worker.runtime import require_job_attempt, require_revision_attempt, selected_revisions, serialized_stage
from worker.services import ffmpeg, storage


@serialized_stage("render")
def render_one(segment_id: str, revision_id: str, attempt_id: str) -> str:
    from worker.tasks.segment import ensure_revision_narrative
    if not ensure_revision_narrative(segment_id, revision_id, attempt_id):
        raise ValueError("Границы не прошли смысловую проверку; рендер запрещён.")
    with session_scope() as db:
        segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
        source = db.get(Source, segment.job.source_id)
        validate_interval(revision.start_sec, revision.end_sec, analysis_duration(segment.job, source))
        already_rendered = bool(
            revision.stages.get("render") == "succeeded"
            and revision.stages.get("verify") == "succeeded"
            and revision.video_key
        )
        if already_rendered:
            return revision.video_key
        if not source.original_key:
            raise RuntimeError("Исходное видео не загружено: доступна только проверенная разметка по расшифровке.")
        revision.status = "processing"
        revision.stages = {**revision.stages, "render": "running"}
        revision.last_activity_at = datetime.now(UTC)
        original_key = source.original_key
        job_id = segment.job_id
        start_sec, end_sec = revision.start_sec, revision.end_sec
        db.commit()

    tmp = Path(tempfile.mkdtemp(prefix=f"render_{revision_id}_"))
    try:
        source_path, output = tmp / "source", tmp / "clip.mp4"
        storage.download_file(original_key, source_path)
        ffmpeg.cut_segment(src=source_path, dst=output, start_sec=start_sec, end_sec=end_sec)
        actual = ffmpeg.probe_duration(output)
        validate_actual_duration(actual)
        ffmpeg.validate_decode(output)
        key = f"jobs/{job_id}/segments/{segment_id}/revisions/{revision_id}/attempts/{attempt_id}/clip.mp4"
        size = storage.upload_file(output, key, "video/mp4")
        with session_scope() as db:
            segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            revision.video_key, revision.actual_duration_sec = key, actual
            revision.validation = {**revision.validation, "technical": {"ok": True, "actual_duration_sec": actual, "decoded": True}}
            revision.stages = {**revision.stages, "render": "succeeded", "verify": "succeeded"}
            revision.last_activity_at = datetime.now(UTC)
            segment.status, segment.error = SegmentStatus.CUT, None
            if not db.execute(select(Asset.id).where(Asset.revision_id == revision.id, Asset.kind == AssetKind.SEGMENT_VIDEO)).first():
                db.add(Asset(job_id=segment.job_id, kind=AssetKind.SEGMENT_VIDEO, s3_key=key, mime="video/mp4", size_bytes=size, segment_id=segment.id, revision_id=revision.id))
            db.commit()
        return key
    except Exception as exc:
        with session_scope() as db:
            segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            revision.status = "failed"
            revision.error = f"render/verify: {exc}"[:1000]
            revision.stages = {**revision.stages, "render": "failed" if not revision.video_key else "succeeded", "verify": "failed"}
            revision.validation = {**revision.validation, "technical": {"ok": False, "reason": str(exc)[:500]}}
            segment.status, segment.error = SegmentStatus.FAILED, revision.error
            db.commit()
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.task(name="worker.tasks.cut.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.CUT, "running", attempt_id=attempt_id, detail="Создаём точные MP4")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        attempt_id = str(job.attempt_id) if job.attempt_id else attempt_id
        rows = [(str(segment.id), str(revision.id), str(revision.attempt_id)) for segment, revision in selected_revisions(db, job)]
    for index, args in enumerate(rows, 1):
        try:
            render_one(*args)
        except Exception:
            pass
        publish_progress(job_id, Stage.CUT, "running", attempt_id=attempt_id, detail="Создаём точные MP4", completed=index, total=len(rows))
    publish_progress(job_id, Stage.CUT, "done", attempt_id=attempt_id, detail="Рендер завершён", completed=len(rows), total=len(rows))
    return job_id
