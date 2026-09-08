from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

from shared.stages import Stage
from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, SegmentStatus
from worker.progress import publish_progress
from worker.runtime import require_job_attempt, require_revision_attempt, selected_revisions, serialized_stage
from worker.services import ffmpeg, storage


def calc_thumbnail_offsets(*, start_sec: float, end_sec: float) -> list[float]:
    duration = end_sec - start_sec
    return [start_sec + duration * fraction for fraction in (0.05, 0.50, 0.95)]


@serialized_stage("thumbnail")
def thumbnail_one(segment_id: str, revision_id: str, attempt_id: str) -> None:
    with session_scope() as db:
        segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
        if revision.stages.get("thumbnail") == "succeeded":
            return
        if revision.stages.get("verify") != "succeeded" or not revision.video_key:
            return
        key = revision.video_key
        duration = revision.actual_duration_sec
        job_id = segment.job_id
        revision.stages = {**revision.stages, "thumbnail": "running"}
        db.commit()
    tmp = Path(tempfile.mkdtemp(prefix=f"thumb_{revision_id}_"))
    try:
        clip = tmp / "clip.mp4"
        storage.download_file(key, clip)
        created: list[tuple[str, int, int]] = []
        for index, offset in enumerate(calc_thumbnail_offsets(start_sec=0, end_sec=duration)):
            output = tmp / f"{index}.jpg"
            ffmpeg.extract_thumbnail(src=clip, dst=output, at_sec=offset)
            object_key = f"jobs/{job_id}/segments/{segment_id}/revisions/{revision_id}/attempts/{attempt_id}/thumb-{index}.jpg"
            created.append((object_key, index, storage.upload_file(output, object_key, "image/jpeg")))
        with session_scope() as db:
            segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            db.execute(delete(Asset).where(Asset.revision_id == revision.id, Asset.kind == AssetKind.THUMBNAIL))
            assets = [Asset(job_id=segment.job_id, kind=AssetKind.THUMBNAIL, s3_key=key, mime="image/jpeg", size_bytes=size, segment_id=segment.id, revision_id=revision.id, position_idx=index) for key, index, size in created]
            db.add_all(assets)
            db.flush()
            segment.selected_thumbnail_id = assets[1].id
            segment.status = SegmentStatus.THUMBNAIL_READY
            revision.stages = {**revision.stages, "thumbnail": "succeeded"}
            revision.last_activity_at = datetime.now(UTC)
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            _, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            revision.stages = {**revision.stages, "thumbnail": "failed"}
            revision.error = f"thumbnail: {exc}"[:1000]
            db.commit()
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.task(name="worker.tasks.thumbnail.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.THUMBNAIL, "running", attempt_id=attempt_id, detail="Создаём превью из готовых роликов")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        rows = [(str(segment.id), str(revision.id), str(revision.attempt_id)) for segment, revision in selected_revisions(db, job)]
    for args in rows:
        try:
            thumbnail_one(*args)
        except Exception:
            pass
    publish_progress(job_id, Stage.THUMBNAIL, "done", attempt_id=attempt_id, detail="Превью готовы")
    return job_id
