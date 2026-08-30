from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus
from worker.progress import publish_progress
from worker.services import ffmpeg, storage, vtt, whisper
from shared.stages import Stage


# Whisper доступен только до этого порога — на CPU и >1ч аудио он OOM-ит.
# Дальше используем VTT (наш парсер чистит rolling-captions и HTML-сущности).
_VTT_DURATION_LIMIT = 18000.0  # 5 ч


@app.task(name="worker.tasks.transcribe.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.TRANSCRIBE, "running")
    with session_scope() as db:
        db.get(Job, job_id).current_stage = Stage.TRANSCRIBE
        db.commit()

    tmp = Path(tempfile.mkdtemp(prefix=f"transcribe_{job_id}_"))
    try:
        with session_scope() as db:
            video_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_VIDEO
                )
            ).scalar_one()
            subs_asset = db.execute(
                select(Asset).where(
                    Asset.job_id == job_id, Asset.kind == AssetKind.SOURCE_SUBS
                )
            ).scalar_one_or_none()
            video_key = video_asset.s3_key
            subs_key = subs_asset.s3_key if subs_asset is not None else None

        mp4 = tmp / "source.mp4"
        storage.download_file(video_key, mp4)
        duration = ffmpeg.probe_duration(mp4)

        use_vtt = subs_key is not None and duration <= _VTT_DURATION_LIMIT
        if use_vtt:
            vtt_text = storage.download_bytes(subs_key).decode("utf-8")
            cues = vtt.parse_vtt(vtt_text)
        else:
            cues = _transcribe_with_whisper(job_id, mp4, tmp)

        transcript_key = f"{job_id}/transcript.json"
        body = json.dumps(cues, ensure_ascii=False).encode("utf-8")
        size = storage.upload_bytes(body, transcript_key, "application/json")
        with session_scope() as db:
            db.add(Asset(
                job_id=job_id,
                kind=AssetKind.TRANSCRIPT,
                s3_key=transcript_key,
                mime="application/json",
                size_bytes=size,
            ))
            db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"transcribe: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.TRANSCRIBE, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.TRANSCRIBE, "done")
    return job_id


def _transcribe_with_whisper(job_id: str, mp4: Path, workdir: Path) -> list[dict]:
    mp3 = workdir / "audio.mp3"
    ffmpeg.extract_audio(src=mp4, dst=mp3)

    audio_key = f"{job_id}/audio.mp3"
    audio_size = storage.upload_file(mp3, audio_key, "audio/mpeg")
    with session_scope() as db:
        db.add(Asset(
            job_id=job_id,
            kind=AssetKind.SOURCE_AUDIO,
            s3_key=audio_key,
            mime="audio/mpeg",
            size_bytes=audio_size,
        ))
        db.commit()

    cues = whisper.transcribe_audio(mp3, language="ru")
    return [dict(c) for c in cues]
