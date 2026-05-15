from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus, Segment, SegmentStatus
from worker.progress import publish_progress
from worker.services import ffmpeg, storage
from shared.stages import Stage


@app.task(name="worker.tasks.cut.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.CUT, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.CUT
        db.commit()

    with session_scope() as db:
        video_asset = db.execute(
            select(Asset).where(
                Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
            )
        ).scalar_one()
        video_key = video_asset.s3_key
        segments = db.execute(
            select(Segment).where(Segment.job_id == job_id).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), float(s.start_sec), float(s.end_sec)) for s in segments
        ]

    tmp = Path(tempfile.mkdtemp(prefix=f"cut_{job_id}_"))
    try:
        mp4 = tmp / "source.mp4"
        storage.download_file(video_key, mp4)

        any_ok = False
        for seg_id, start, end in segment_data:
            out = tmp / f"{seg_id}.mp4"
            try:
                ffmpeg.cut_segment(src=mp4, dst=out, start_sec=start, end_sec=end)
                key = f"{job_id}/segments/{seg_id}.mp4"
                size = storage.upload_file(out, key, "video/mp4")
                with session_scope() as db:
                    db.add(Asset(
                        job_id=job_id,
                        kind=AssetKind.SEGMENT_VIDEO,
                        s3_key=key,
                        mime="video/mp4",
                        size_bytes=size,
                        segment_id=seg_id,
                    ))
                    db.get(Segment, seg_id).status = SegmentStatus.CUT
                    db.commit()
                any_ok = True
            except Exception as exc:
                with session_scope() as db:
                    seg = db.get(Segment, seg_id)
                    seg.status = SegmentStatus.FAILED
                    seg.error = f"cut: {exc}"[:500]
                    db.commit()

        if not any_ok:
            raise RuntimeError("cut: all segments failed")
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"cut: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.CUT, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.CUT, "done")
    return job_id
