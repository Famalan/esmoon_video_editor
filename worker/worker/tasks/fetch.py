from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from shared.stages import Stage
from worker.celery_app import app
from worker.db import session_scope
from worker.models import JobStatus, Source
from worker.progress import publish_progress
from worker.runtime import advisory_lock, as_uuid, require_job_attempt
from worker.services import ffmpeg, storage


class _SourceReady(RuntimeError):
    pass


def _ytdlp_metadata(url: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--no-warnings", "--dump-single-json", url], capture_output=True, text=True)
    if result.returncode:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def _ytdlp_subtitles(url: str, workdir: Path) -> tuple[dict, Path]:
    """Fetch public metadata and timestamped captions without media bytes."""
    info_result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--skip-download", "--no-warnings", "--dump-single-json", url],
        capture_output=True,
        text=True,
    )
    if info_result.returncode:
        detail = (info_result.stderr or info_result.stdout or "")[-700:]
        if "not a bot" in detail.lower() or "sign in" in detail.lower():
            raise RuntimeError("YouTube не отдал расшифровку без авторизации. Прикрепите расшифровку с таймкодами.")
        raise RuntimeError(f"Не удалось получить данные YouTube без скачивания видео: {detail}")
    try:
        info = json.loads(info_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("YouTube вернул некорректные данные о видео.") from exc

    subtitle_result = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp", "--skip-download", "--write-auto-subs", "--write-subs",
            "--sub-langs", "ru,ru-orig", "--sub-format", "vtt", "--no-warnings", "--quiet",
            "-o", str(workdir / "subs"), url,
        ],
        capture_output=True,
        text=True,
    )
    subtitles = sorted(workdir.glob("subs*.vtt"))
    if subtitle_result.returncode or not subtitles:
        detail = (subtitle_result.stderr or subtitle_result.stdout or "")[-700:]
        if "not a bot" in detail.lower() or "sign in" in detail.lower():
            raise RuntimeError("YouTube не отдал расшифровку без авторизации. Прикрепите расшифровку с таймкодами.")
        raise RuntimeError("У видео нет доступной расшифровки с таймкодами. Прикрепите её вручную, чтобы выполнить разметку без видео.")
    return info, subtitles[0]


def _ytdlp_download(url: str, workdir: Path) -> tuple[Path, Path | None]:
    vid = subprocess.run([
        sys.executable, "-m", "yt_dlp", "-f", "bestvideo[height<=1080]+bestaudio/best",
        "--merge-output-format", "mp4", "--retries", "5", "--fragment-retries", "5",
        "--retry-sleep", "http:3", "-o", str(workdir / "source.%(ext)s"), "--no-warnings", "--quiet", url,
    ], capture_output=True, text=True)
    if vid.returncode:
        raise RuntimeError(f"yt-dlp video failed: {(vid.stderr or '')[-400:]}")
    candidates = [p for p in workdir.glob("source.*") if p.is_file()]
    if not candidates:
        raise RuntimeError("yt-dlp produced no source file")
    subprocess.run([
        sys.executable, "-m", "yt_dlp", "--skip-download", "--write-auto-subs", "--write-subs",
        "--retries", "5", "--sub-lang", "ru", "--sub-format", "vtt", "-o", str(workdir / "subs"),
        "--no-warnings", "--quiet", url,
    ], capture_output=True, text=True)
    subtitles = list(workdir.glob("subs*.vtt"))
    return candidates[0], subtitles[0] if subtitles else None


@app.task(name="worker.tasks.fetch.run")
def run(job_id: str, attempt_id: str | None = None) -> str:
    publish_progress(job_id, Stage.FETCH, "running", attempt_id=attempt_id, detail="Проверяем исходное видео")
    with session_scope() as db:
        job = require_job_attempt(db, job_id, attempt_id)
        attempt_id = str(job.attempt_id) if job.attempt_id else attempt_id
        if not job.source_id:
            raise RuntimeError("job has no source")
        source_id = str(job.source_id)
        source = db.get(Source, job.source_id)
        source_state = (source.status, source.duration_sec, source.original_key, source.preview_status, source.preview_key)
        job.status = JobStatus.RUNNING
        db.commit()
    status, duration, original_key, preview_status, preview_key = source_state

    # URL jobs are transcript-first. If no immutable original exists, deliberately
    # stop after captions: the pipeline can produce reviewed intervals without
    # downloading video or audio and may render later when media is attached.
    if not original_key:
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            source = db.get(Source, job.source_id)
            source_url = source.source_url
            source_type = source.source_type
            transcript_cached = bool(source.transcript_key and storage.object_exists(source.transcript_key))
            subs_cached = bool(source.subs_key and storage.object_exists(source.subs_key))
            if transcript_cached or subs_cached:
                source.status, source.error = "transcript_ready", None
                source.updated_at = datetime.now(UTC)
                db.commit()
        if source_url and source_type == "url" and not (transcript_cached or subs_cached):
            try:
                with advisory_lock("source", source_id) as db:
                    job = require_job_attempt(db, job_id, attempt_id, lock=False)
                    source = db.get(Source, as_uuid(source_id))
                    if not ((source.transcript_key and storage.object_exists(source.transcript_key))
                            or (source.subs_key and storage.object_exists(source.subs_key))):
                        tmp = Path(tempfile.mkdtemp(prefix=f"captions_{source_id}_"))
                        try:
                            info, subtitles = _ytdlp_subtitles(source_url, tmp)
                            key = f"sources/{source.id}/subs/{job.attempt_id}.vtt"
                            storage.upload_file(subtitles, key, "text/vtt")
                            source.subs_key = key
                            source.title = str(info.get("title") or source.title or "YouTube видео")[:500]
                            source.status, source.error = "transcript_ready", None
                            source.updated_at = datetime.now(UTC)
                        finally:
                            shutil.rmtree(tmp, ignore_errors=True)
            except Exception as exc:
                with session_scope() as db:
                    job = require_job_attempt(db, job_id, attempt_id)
                    job.status, job.error = JobStatus.FAILED, f"fetch: {exc}"[:1000]
                    source = db.get(Source, job.source_id)
                    source.status, source.error = "failed", str(exc)[:1000]
                    db.commit()
                publish_progress(job_id, Stage.FETCH, "failed", attempt_id=attempt_id, detail=str(exc)[:300])
                raise
        elif not source_url:
            raise RuntimeError("Загруженный файл не найден в хранилище")
        publish_progress(job_id, Stage.FETCH, "done", attempt_id=attempt_id, detail="Расшифровка получена без скачивания видео")
        return job_id

    cached = bool(status == "ready" and duration and original_key and storage.object_exists(original_key)
                  and preview_status == "ready" and preview_key and storage.object_exists(preview_key))

    if cached:
        with advisory_lock("source", source_id) as db:
            source = db.get(Source, as_uuid(source_id))
            if source.source_url and (not source.title or source.title.startswith("YouTube ·")):
                info = _ytdlp_metadata(source.source_url)
                source.title = str(info.get("title") or source.title)[:500]
        publish_progress(job_id, Stage.FETCH, "done", attempt_id=attempt_id, detail="Используем сохранённый исходник")
        return job_id

    try:
        with advisory_lock("source", source_id) as db:
            job = require_job_attempt(db, job_id, attempt_id, lock=False)
            source = db.get(Source, as_uuid(source_id))
            source_url, original_key = source.source_url, source.original_key
            if (source.status == "ready" and source.duration_sec and original_key and storage.object_exists(original_key)
                    and source.preview_status == "ready" and source.preview_key and storage.object_exists(source.preview_key)):
                raise _SourceReady()
            source.ingest_attempt_id = job.attempt_id
            source.updated_at = datetime.now(UTC)
            # Keep the advisory lock, but defer the Source row UPDATE until publish;
            # a new analysis must not wait on a long preview encode to allocate its run.
            tmp = Path(tempfile.mkdtemp(prefix=f"source_{source_id}_"))
            try:
                local = tmp / "source"
                subs: Path | None = None
                if original_key and storage.object_exists(original_key):
                    storage.download_file(original_key, local)
                else:
                    if not source_url:
                        raise RuntimeError("Загруженный файл не найден в хранилище")
                    local, subs = _ytdlp_download(source_url, tmp)
                    immutable_key = f"sources/{source.id}/original/{job.attempt_id}{local.suffix or '.mp4'}"
                    size = storage.upload_file(local, immutable_key, "video/mp4")
                    source.original_key, source.original_mime, source.size_bytes = immutable_key, "video/mp4", size
                    if subs:
                        subs_key = f"sources/{source.id}/subs/{job.attempt_id}.vtt"
                        storage.upload_file(subs, subs_key, "text/vtt")
                        source.subs_key = subs_key
                media = ffmpeg.probe_media(local)
                if not any(s.get("codec_type") == "video" for s in media.get("streams", [])):
                    raise RuntimeError("В исходнике нет читаемого видеопотока")
                source.duration_sec = media["duration"]
                if source_url:
                    info = _ytdlp_metadata(source_url)
                    source.title = str(info.get("title") or source.title or source.filename or "Видео")[:500]
                else:
                    source.title = source.title or source.filename or "Локальное видео"
                if ffmpeg.is_browser_compatible(local):
                    source.preview_key = source.original_key
                elif not source.preview_key or not storage.object_exists(source.preview_key):
                    source.preview_status = "processing"
                    preview = tmp / "preview.mp4"
                    ffmpeg.make_preview(src=local, dst=preview)
                    preview_key = f"sources/{source.id}/preview/{job.attempt_id}.mp4"
                    storage.upload_file(preview, preview_key, "video/mp4")
                    source.preview_key = preview_key
                source.preview_status, source.status, source.error = "ready", "ready", None
                source.updated_at = datetime.now(UTC)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except _SourceReady:
        publish_progress(job_id, Stage.FETCH, "done", attempt_id=attempt_id, detail="Используем сохранённый исходник")
        return job_id
    except Exception as exc:
        with session_scope() as db:
            job = require_job_attempt(db, job_id, attempt_id)
            job.status, job.error = JobStatus.FAILED, f"fetch: {exc}"[:1000]
            source = db.get(Source, job.source_id)
            source.status, source.error = "failed", str(exc)[:1000]
            db.commit()
        publish_progress(job_id, Stage.FETCH, "failed", attempt_id=attempt_id, detail=str(exc)[:300])
        raise
    publish_progress(job_id, Stage.FETCH, "done", attempt_id=attempt_id, detail="Исходник готов")
    return job_id
