from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from worker.celery_app import app
from worker.db import session_scope
from worker.models import Asset, AssetKind, Job, JobStatus
from worker.progress import publish_progress
from worker.services import storage
from shared.stages import Stage


def _ytdlp_download(url: str, workdir: Path) -> tuple[Path, Path | None]:
    """Возвращает (path_to_mp4, path_to_vtt_or_None). Бросает RuntimeError при ошибке."""
    video_tmpl = str(workdir / "source.%(ext)s")
    vid = subprocess.run(
        [
            "yt-dlp",
            "-f", "bestvideo[height<=1080]+bestaudio/best",
            "--merge-output-format", "mp4",
            "-o", video_tmpl,
            "--no-warnings",
            "--quiet",
            url,
        ],
        capture_output=True,
        text=True,
    )
    if vid.returncode != 0:
        raise RuntimeError(f"yt-dlp video failed: {(vid.stderr or '')[-400:]}")

    mp4 = workdir / "source.mp4"
    if not mp4.exists():
        candidates = list(workdir.glob("source.*"))
        if not candidates:
            raise RuntimeError("yt-dlp produced no source file")
        mp4 = candidates[0]

    subs_tmpl = str(workdir / "subs")
    subprocess.run(
        [
            "yt-dlp",
            "--skip-download",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang", "ru",
            "--sub-format", "vtt",
            "-o", subs_tmpl,
            "--no-warnings",
            "--quiet",
            url,
        ],
        capture_output=True,
        text=True,
    )
    vtt_files = list(workdir.glob("subs*.vtt"))
    return mp4, (vtt_files[0] if vtt_files else None)


@app.task(name="worker.tasks.fetch.run")
def run(job_id: str) -> str:
    publish_progress(job_id, Stage.FETCH, "running")

    with session_scope() as db:
        job = db.get(Job, job_id)
        job.status = JobStatus.RUNNING
        job.current_stage = Stage.FETCH
        url = job.source_url
        db.commit()

    if not url:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = "fetch: source_url is required in Phase 2"
            db.commit()
        publish_progress(job_id, Stage.FETCH, "failed")
        raise RuntimeError("source_url required")

    tmp = Path(tempfile.mkdtemp(prefix=f"fetch_{job_id}_"))
    try:
        mp4, vtt = _ytdlp_download(url, tmp)

        video_key = f"{job_id}/source.mp4"
        size = storage.upload_file(mp4, video_key, "video/mp4")

        with session_scope() as db:
            db.add(Asset(
                job_id=job_id,
                kind=AssetKind.SOURCE_VIDEO,
                s3_key=video_key,
                mime="video/mp4",
                size_bytes=size,
            ))
            db.commit()

        if vtt is not None:
            subs_key = f"{job_id}/subs.vtt"
            subs_size = storage.upload_file(vtt, subs_key, "text/vtt")
            with session_scope() as db:
                db.add(Asset(
                    job_id=job_id,
                    kind=AssetKind.SOURCE_SUBS,
                    s3_key=subs_key,
                    mime="text/vtt",
                    size_bytes=subs_size,
                ))
                db.commit()
    except Exception as exc:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status = JobStatus.FAILED
            job.error = f"fetch: {exc}"[:1000]
            db.commit()
        publish_progress(job_id, Stage.FETCH, "failed")
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    publish_progress(job_id, Stage.FETCH, "done")
    return job_id
