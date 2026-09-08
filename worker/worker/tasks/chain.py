from __future__ import annotations

from shared.stages import Stage
from shared.policy import POLICY
from worker.celery_app import app
from worker.db import session_scope
from worker.models import Job, JobStatus, Source
from worker.progress import publish_progress, publish_readiness as update_readiness
from worker.runtime import StaleAttempt, advisory_lock, aggregate_job, require_job_attempt, require_revision_attempt, selected_revisions, session_advisory_lock
from worker.tasks import cut, fetch, metadata, segment, thumbnail, transcribe


@app.task(name="worker.tasks.chain.publish_readiness")
def publish_readiness() -> dict:
    return update_readiness()


@app.task(name="worker.tasks.chain.build_pipeline")
def build_pipeline(job_id: str, attempt_id: str | None = None) -> str:
    with advisory_lock("job", job_id) as db:
        job = require_job_attempt(db, job_id, attempt_id)
        attempt_id = str(job.attempt_id) if job.attempt_id else attempt_id
        if job.status == JobStatus.SUCCEEDED:
            return job_id
        if not job.policy_snapshot or job.policy_snapshot.get("prompt_version") != POLICY["prompt_version"]:
            job.status = JobStatus.FAILED
            job.error = "Этот запуск использует другую версию промпта. Создайте новый анализ: предыдущие файлы сохранены."
            return job_id
        if (job.progress or {}).get("pipeline_running") and (job.progress or {}).get("pipeline_attempt") == attempt_id:
            return job_id
        job.status, job.error = JobStatus.RUNNING, None
        job.progress = {**(job.progress or {}), "pipeline_running": True, "pipeline_attempt": attempt_id}
    try:
        fetch.run(job_id, attempt_id)
        transcribe.run(job_id, attempt_id)
        segment.run(job_id, attempt_id)
        with session_scope() as db:
            current = require_job_attempt(db, job_id, attempt_id)
            source = db.get(Source, current.source_id)
            media_available = bool(source and source.original_key)
            recoverable_clips = False
            if not media_available:
                for _, revision in selected_revisions(db, current):
                    if revision.video_key and revision.validation.get("technical", {}).get("ok") is True:
                        recoverable_clips = True
                        continue
                    if revision.validation.get("narrative", {}).get("ok") is True:
                        revision.status = "analyzed"
                        revision.stages = {name: "not_requested" for name in ("render", "verify", "thumbnail", "metadata")}
                current.progress = {**(current.progress or {}), "media_deferred": not media_available}
                db.commit()
        if media_available:
            cut.run(job_id, attempt_id)
            thumbnail.run(job_id, attempt_id)
            metadata.run(job_id, attempt_id)
        elif recoverable_clips:
            # Old verified clip revisions can still recover thumbnails/metadata;
            # neither operation needs the source original.
            thumbnail.run(job_id, attempt_id)
            metadata.run(job_id, attempt_id)
        finalize(job_id, attempt_id)
    except StaleAttempt:
        return job_id
    except Exception as exc:
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)[:1000]
            db.commit()
        raise
    finally:
        with session_scope() as db:
            try:
                job = require_job_attempt(db, job_id, attempt_id)
            except StaleAttempt:
                job = None
            if job is not None:
                job.progress = {**(job.progress or {}), "pipeline_running": False}
                db.commit()
    return job_id


@app.task(name="worker.tasks.chain.render_revision")
def render_revision(segment_id: str, revision_id: str, attempt_id: str) -> str:
    with session_advisory_lock("revision", revision_id):
        with session_scope() as db:
            seg_obj, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
            job_uuid = seg_obj.job_id
            job_id = str(job_uuid)
            job_obj = db.get(Job, job_uuid)
            source = db.get(Source, job_obj.source_id)
            job_attempt = str(job_obj.attempt_id)
            has_source = bool(source and source.original_key)
            has_verified_clip = bool(revision.video_key and revision.validation.get("technical", {}).get("ok") is True)
        stage = Stage.CUT if has_source or has_verified_clip else Stage.SEGMENT
        detail = "Пересоздаём эпизод" if stage == Stage.CUT else "Проверяем границы эпизода"
        publish_progress(job_id, stage, "running", attempt_id=job_attempt, detail=f"{detail} {segment_id}")
        try:
            if not segment.ensure_revision_narrative(segment_id, revision_id, attempt_id):
                aggregate_job(job_id, job_attempt)
                publish_progress(job_id, Stage.DONE, "failed", attempt_id=job_attempt, detail="Границы не прошли смысловую проверку")
                return revision_id
            if not has_source and not has_verified_clip:
                with session_scope() as db:
                    _, revision = require_revision_attempt(db, segment_id, revision_id, attempt_id)
                    revision.status = "analyzed"
                    revision.error = None
                    revision.stages = {name: "not_requested" for name in ("render", "verify", "thumbnail", "metadata")}
                    job_obj = db.get(Job, job_uuid)
                    job_obj.progress = {**(job_obj.progress or {}), "media_deferred": True}
                    db.commit()
            elif has_source:
                cut.render_one(segment_id, revision_id, attempt_id)
            if has_source or has_verified_clip:
                thumbnail.thumbnail_one(segment_id, revision_id, attempt_id)
                metadata.metadata_one(segment_id, revision_id, attempt_id)
        except StaleAttempt:
            return revision_id
        except Exception:
            pass
        aggregate_job(job_id, job_attempt)
        publish_progress(job_id, Stage.DONE, "done", attempt_id=job_attempt, detail="Версия эпизода обработана")
    return revision_id


@app.task(name="worker.tasks.chain.finalize")
def finalize(job_id: str, attempt_id: str | None = None) -> str:
    status = aggregate_job(job_id, attempt_id)
    label = "succeeded" if status == JobStatus.SUCCEEDED else status.value
    publish_progress(job_id, Stage.DONE, status=label, attempt_id=attempt_id, detail="Обработка завершена")
    return job_id
