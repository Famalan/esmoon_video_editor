from __future__ import annotations

from datetime import UTC, datetime

from shared.stages import Stage
from worker.celery_app import app
from worker.config import settings
from worker.db import session_scope
from worker.models import SegmentStatus
from worker.progress import publish_progress
from worker.prompts import metadata as prompt
from worker.runtime import require_job_attempt, require_revision_attempt, selected_revisions, serialized_stage
from worker.services import llm


@serialized_stage("metadata")
def metadata_one(segment_id: str, revision_id: str, attempt_id: str) -> None:
    with session_scope() as db:
        segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
        if revision.stages.get("metadata") == "succeeded":
            return
        if revision.stages.get("verify") != "succeeded":
            return
        title, summary, transcript = segment.title or "", segment.summary or "", revision.transcript_text or ""
        revision.stages = {**revision.stages, "metadata": "running"}
        db.commit()
    try:
        data = llm.call_json(system=prompt.SYSTEM, user=prompt.USER_TEMPLATE.format(segment_title=title, segment_summary=summary, segment_transcript=transcript), schema=prompt.JSON_SCHEMA, schema_name="youtube_metadata", model=settings.codex_model)
        with session_scope() as db:
            segment, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            manual = set(revision.manual_fields or [])
            if "yt_title" not in manual:
                revision.yt_title = data["title"]
            if "yt_description" not in manual:
                revision.yt_description = data["description"]
            if "yt_tags" not in manual:
                revision.yt_tags = data["tags"]
            # A boundary edit sets this flag. Respect any later user acknowledgement.
            revision.stages = {**revision.stages, "metadata": "succeeded"}
            revision.status = "ready" if all(revision.stages.get(name) == "succeeded" for name in ("render", "verify", "thumbnail", "metadata")) else "failed"
            revision.error = None if revision.status == "ready" else (revision.error or "Не все этапы обработки завершились успешно.")
            revision.last_activity_at = datetime.now(UTC)
            segment.status, segment.error = SegmentStatus.METADATA_READY, None
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            _, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            revision.stages = {**revision.stages, "metadata": "failed"}
            revision.status = "failed"
            revision.error = f"metadata: {exc}"[:1000]
            db.commit()
        raise


@app.task(name="worker.tasks.metadata.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.METADATA, "running", attempt_id=attempt_id, detail="Создаём метаданные по полному тексту")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        rows = [(str(segment.id), str(revision.id), str(revision.attempt_id)) for segment, revision in selected_revisions(db, job)]
    for index, args in enumerate(rows, 1):
        try:
            metadata_one(*args)
        except Exception:
            pass
        publish_progress(job_id, Stage.METADATA, "running", attempt_id=attempt_id, detail="Метаданные по полному тексту", completed=index, total=len(rows))
    publish_progress(job_id, Stage.METADATA, "done", attempt_id=attempt_id, detail="Метаданные обработаны")
    return job_id
