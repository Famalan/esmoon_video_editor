from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import (
    Asset,
    AssetKind,
    Job,
    JobStatus,
    Segment,
    SegmentDecision,
    SegmentStatus,
)
from worker.progress import publish_progress
from worker.services import ffmpeg, storage
from shared.stages import Stage


def calc_thumbnail_offsets(*, start_sec: float, end_sec: float) -> list[float]:
    duration = end_sec - start_sec
    return [start_sec + duration * frac for frac in (0.05, 0.50, 0.95)]


@app.task(name="worker.tasks.thumbnail.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.THUMBNAIL, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.THUMBNAIL
        db.commit()

    with session_scope() as db:
        video_asset = db.execute(
            select(Asset).where(
                Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
            )
        ).scalar_one()
        video_key = video_asset.s3_key
        segments = db.execute(
            select(Segment).where(
                Segment.job_id == job_id,
                Segment.decision == SegmentDecision.PUBLISH,
                Segment.status == SegmentStatus.CUT,
            ).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), float(s.start_sec), float(s.end_sec)) for s in segments
        ]

    if not segment_data:
        publish_progress(job_id, Stage.THUMBNAIL, "done")
        return job_id

    tmp = Path(tempfile.mkdtemp(prefix=f"thumb_{job_id}_"))
    try:
        mp4 = tmp / "source.mp4"
        storage.download_file(video_key, mp4)

        for seg_id, start, end in segment_data:
            offsets = calc_thumbnail_offsets(start_sec=start, end_sec=end)
            asset_ids: list[str] = []
            for idx, off in enumerate(offsets):
                out = tmp / f"{seg_id}_{idx}.jpg"
                try:
                    ffmpeg.extract_thumbnail(src=mp4, dst=out, at_sec=off)
                    key = f"{job_id}/thumbnails/{seg_id}/{idx}.jpg"
                    size = storage.upload_file(out, key, "image/jpeg")
                    with session_scope() as db:
                        a = Asset(
                            job_id=job_id,
                            kind=AssetKind.THUMBNAIL,
                            s3_key=key,
                            mime="image/jpeg",
                            size_bytes=size,
                            segment_id=seg_id,
                            position_idx=idx,
                        )
                        db.add(a)
                        db.commit()
                        asset_ids.append(str(a.id))
                except Exception as exc:
                    with session_scope() as db:
                        seg = db.get(Segment, seg_id)
                        seg.error = (seg.error or "") + f" thumb{idx}: {exc};"
                        db.commit()

            if asset_ids:
                middle_asset_id = asset_ids[1] if len(asset_ids) >= 2 else asset_ids[0]
                with session_scope() as db:
                    seg = db.get(Segment, seg_id)
                    seg.selected_thumbnail_id = middle_asset_id
                    seg.status = SegmentStatus.THUMBNAIL_READY
                    db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"thumbnail: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.THUMBNAIL, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.THUMBNAIL, "done")
    return job_id
