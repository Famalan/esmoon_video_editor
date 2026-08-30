from __future__ import annotations

from sqlalchemy import select

from worker.celery_app import app
from worker.config import settings
from worker.db import session_scope
from worker.models import (
    Job,
    Segment,
    SegmentDecision,
    SegmentStatus,
    Upload,
    UploadStatus,
)
from worker.progress import publish_progress
from worker.prompts import metadata as prompt
from worker.services import llm
from shared.stages import Stage


@app.task(name="worker.tasks.metadata.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.METADATA, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.METADATA
        db.commit()

    with session_scope() as db:
        segments = db.execute(
            select(Segment).where(
                Segment.job_id == job_id,
                Segment.decision == SegmentDecision.PUBLISH,
                Segment.status == SegmentStatus.THUMBNAIL_READY,
            ).order_by(Segment.index)
        ).scalars().all()
        segment_data = [
            (str(s.id), s.title or "", s.summary or "", s.transcript_excerpt or "")
            for s in segments
        ]

    for seg_id, title, summary, excerpt in segment_data:
        try:
            user = prompt.USER_TEMPLATE.format(
                segment_title=title,
                segment_summary=summary,
                segment_transcript=excerpt,
            )
            data = llm.call_json(
                system=prompt.SYSTEM, user=user,
                schema=prompt.JSON_SCHEMA, schema_name="youtube_metadata",
                model=settings.codex_model,
            )
            with session_scope() as db:
                upload = db.execute(
                    select(Upload).where(Upload.segment_id == seg_id)
                ).scalar_one_or_none()
                if upload is None:
                    upload = Upload(segment_id=seg_id)
                    db.add(upload)
                upload.youtube_title = data["title"]
                upload.youtube_description = data["description"]
                upload.tags = data["tags"]
                upload.status = UploadStatus.PENDING
                db.get(Segment, seg_id).status = SegmentStatus.METADATA_READY
                db.commit()
        except Exception as exc:
            with session_scope() as db:
                seg = db.get(Segment, seg_id)
                seg.error = (seg.error or "") + f" metadata: {exc};"
                db.commit()

    publish_progress(job_id, Stage.METADATA, "done")
    return job_id
